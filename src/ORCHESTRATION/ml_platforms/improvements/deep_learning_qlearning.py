# coding: utf-8
"""
Deep Learning Q-Learning Table Module

Q-Learning system optimized for deep learning framework selection
with tabular state-action value representation.

Features:
1. Dense Q-Table - Full state-action matrix for deep learning tasks
2. Experience Replay - Stable learning from past experiences
3. Target Network - Periodic updates for convergence
4. Double Q-Learning - Reduces overestimation bias
5. Prioritized Replay - Importance-weighted sampling
6. Epsilon Decay - Adaptive exploration/exploitation

Author: Azazael System
Date: 2026-02-10
Version: 1.0.0
"""

from typing import Any, List
from dataclasses import dataclass
import numpy as np
import json
import time

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class DLTaskType:
    """Deep learning task types."""

    IMAGE_CLASSIFICATION = "image_classification"
    OBJECT_DETECTION = "object_detection"
    SEMANTIC_SEGMENTATION = "semantic_segmentation"
    INSTANCE_SEGMENTATION = "instance_segmentation"
    POSE_ESTIMATION = "pose_estimation"
    IMAGE_GENERATION = "image_generation"
    TEXT_CLASSIFICATION = "text_classification"
    NAMED_ENTITY_RECOGNITION = "named_entity_recognition"
    QUESTION_ANSWERING = "question_answering"
    SUMMARIZATION = "summarization"
    MACHINE_TRANSLATION = "machine_translation"
    SPEECH_RECOGNITION = "speech_recognition"
    SPEECH_SYNTHESIS = "speech_synthesis"
    TIME_SERIES_FORECASTING = "time_series_forecasting"
    ANOMALY_DETECTION = "anomaly_detection"
    RECOMMENDATION = "recommendation"
    REINFORCEMENT_LEARNING = "reinforcement_learning"
    MULTIMODAL = "multimodal"


class DLFramework:
    """Deep learning frameworks."""

    PYTORCH = "pytorch"
    TENSORFLOW = "tensorflow"
    KERAS = "keras"
    JAX = "jax"
    FLAX = "flax"
    PADDLEPADDLE = "paddlepaddle"
    MINDSPORE = "mindspore"
    CAFFE = "caffe"
    CAFFE2 = "caffe2"
    MXNET = "mxnet"
    TORCH = "torch"
    DIFFUSERS = "diffusers"
    TRANSFORMERS = "transformers"


class HardwareType:
    """Hardware types for training."""

    CPU = "cpu"
    NVIDIA_GPU = "nvidia_gpu"
    AMD_GPU = "amd_gpu"
    TPU = "tpu"
    MULTI_GPU = "multi_gpu"
    CLUSTER = "cluster"


@dataclass
class DLTransition:
    """Transition for experience replay."""

    state: int
    action: int
    reward: float
    next_state: int
    done: bool
    priority: float = 1.0
    timestamp: float = 0.0
    task_type: (str | None) = None
    framework: (str | None) = None
    hardware: (str | None) = None

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class DLQTable:
    """Dense Q-Table for Deep Learning Framework Selection."""

    def __init__(
        self,
        num_states: int = 256,
        num_actions: int = 20,
        alpha: float = 0.1,
        gamma: float = 0.95,
    ):
        self.num_states = num_states
        self.num_actions = num_actions
        self.alpha = alpha
        self.gamma = gamma
        self.q_table = np.zeros((num_states, num_actions))
        self.target_table = np.zeros((num_states, num_actions))
        self.state_visits = np.zeros(num_states)
        self.action_counts = np.zeros(num_actions)
        self.training_steps = 0

    def get_q_values(self, state: int) -> np.ndarray:
        return self.q_table[state]

    def get_best_action(
        self, state: int, exploit: bool = False, epsilon: float = 0.1
    ) -> int:
        if not exploit and np.random.random() < epsilon:
            return np.random.randint(0, self.num_actions)
        return int(np.argmax(self.q_table[state]))

    def update(
        self,
        state: int,
        action: int,
        reward: float,
        next_state: int,
        done: bool = False,
    ) -> float:
        best_action = np.argmax(self.q_table[next_state])
        td_target = (
            reward
            + (1.0 - done) * self.gamma * self.target_table[next_state, best_action]
        )
        td_error = td_target - self.q_table[state, action]
        self.q_table[state, action] += self.alpha * td_error
        self.state_visits[state] += 1
        self.action_counts[action] += 1
        self.training_steps += 1
        return td_error

    def update_target(self, tau: float = 0.005):
        for state in range(self.num_states):
            for action in range(self.num_actions):
                self.target_table[state, action] = (
                    tau * self.q_table[state, action]
                    + (1.0 - tau) * self.target_table[state, action]
                )

    def hard_update_target(self):
        self.target_table = np.copy(self.q_table)

    def save(self, path: str):
        data = {
            "q_table": self.q_table.tolist(),
            "target_table": self.target_table.tolist(),
            "state_visits": self.state_visits.tolist(),
            "action_counts": self.action_counts.tolist(),
            "training_steps": self.training_steps,
            "num_states": self.num_states,
            "num_actions": self.num_actions,
            "alpha": self.alpha,
            "gamma": self.gamma,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.q_table = np.array(data["q_table"])
        self.target_table = np.array(data.get("target_table", data["q_table"]))
        self.state_visits = np.array(
            data.get("state_visits", np.zeros(self.num_states))
        )
        self.action_counts = np.array(
            data.get("action_counts", np.zeros(self.num_actions))
        )
        self.training_steps = data.get("training_steps", 0)


class ExperienceReplayBuffer:
    """Experience Replay Buffer for stable Q-learning."""

    def __init__(
        self,
        capacity: int = 10000,
        alpha: float = 0.6,
        beta_start: float = 0.4,
        beta_frames: int = 100000,
    ):
        self.capacity = capacity
        self.buffer: list[DLTransition] = []
        self.priorities: list[float] = []
        self.alpha = alpha
        self.beta_start = beta_start
        self.beta_frames = beta_frames
        self.frame = 1

    def push(
        self,
        state: int,
        action: int,
        reward: float,
        next_state: int,
        done: bool,
        task_type: (str | None) = None,
        framework: (str | None) = None,
        hardware: (str | None) = None,
    ):
        transition = DLTransition(
            state=state,
            action=action,
            reward=reward,
            next_state=next_state,
            done=done,
            priority=self._get_max_priority(),
            task_type=task_type,
            framework=framework,
            hardware=hardware,
        )
        self.buffer.append(transition)
        self.priorities.append(transition.priority)
        if len(self.buffer) > self.capacity:
            self.buffer.pop(0)
            self.priorities.pop(0)

    def _get_max_priority(self) -> float:
        return max(self.priorities) if self.priorities else 1.0

    def sample(
        self, batch_size: int
    ) -> tuple[list[DLTransition], np.ndarray, np.ndarray]:
        if len(self.buffer) < batch_size:
            batch_size = len(self.buffer)

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
        weights = weights / (weights.max() + 1e-10)

        return samples, weights, np.array(indices)

    def update_priorities(self, indices: list[int], priorities: np.ndarray):
        for i, priority in zip(indices, priorities):
            self.priorities[i] = priority + 1e-6

    def __len__(self):
        return len(self.buffer)


class DeepLearningQLearning:
    """Complete Q-Learning system for Deep Learning Framework Selection."""

    FRAMEWORK_CONFIGS = {
        DLFramework.PYTORCH: {
            "training_speed": 0.85,
            "inference_speed": 0.88,
            "gpu_support": 0.95,
            "ease_of_use": 0.90,
            "community": 0.95,
            "pretrained_models": 0.98,
            "debugging": 0.85,
            "deployment": 0.88,
        },
        DLFramework.TENSORFLOW: {
            "training_speed": 0.80,
            "inference_speed": 0.92,
            "gpu_support": 0.90,
            "ease_of_use": 0.85,
            "community": 0.90,
            "pretrained_models": 0.92,
            "debugging": 0.80,
            "deployment": 0.95,
        },
        DLFramework.KERAS: {
            "training_speed": 0.75,
            "inference_speed": 0.85,
            "gpu_support": 0.85,
            "ease_of_use": 0.98,
            "community": 0.88,
            "pretrained_models": 0.90,
            "debugging": 0.92,
            "deployment": 0.88,
        },
        DLFramework.JAX: {
            "training_speed": 0.90,
            "inference_speed": 0.85,
            "gpu_support": 0.92,
            "ease_of_use": 0.70,
            "community": 0.75,
            "pretrained_models": 0.60,
            "debugging": 0.60,
            "deployment": 0.70,
        },
        DLFramework.TRANSFORMERS: {
            "training_speed": 0.70,
            "inference_speed": 0.78,
            "gpu_support": 0.88,
            "ease_of_use": 0.88,
            "community": 0.98,
            "pretrained_models": 1.00,
            "debugging": 0.75,
            "deployment": 0.82,
        },
        DLFramework.DIFFUSERS: {
            "training_speed": 0.65,
            "inference_speed": 0.70,
            "gpu_support": 0.88,
            "ease_of_use": 0.82,
            "community": 0.85,
            "pretrained_models": 0.95,
            "debugging": 0.72,
            "deployment": 0.75,
        },
    }

    TASK_FRAMEWORKS = {
        DLTaskType.IMAGE_CLASSIFICATION: [
            "pytorch",
            "tensorflow",
            "keras",
            "transformers",
        ],
        DLTaskType.OBJECT_DETECTION: ["pytorch", "tensorflow", "detectron2"],
        DLTaskType.SEMANTIC_SEGMENTATION: [
            "pytorch",
            "tensorflow",
            "segmentation_models",
        ],
        DLTaskType.IMAGE_GENERATION: ["pytorch", "diffusers", "tensorflow", "jax"],
        DLTaskType.TEXT_CLASSIFICATION: [
            "transformers",
            "pytorch",
            "tensorflow",
            "keras",
        ],
        DLTaskType.NAMED_ENTITY_RECOGNITION: ["transformers", "spacy", "pytorch"],
        DLTaskType.QUESTION_ANSWERING: ["transformers", "pytorch", "tensorflow"],
        DLTaskType.SUMMARIZATION: ["transformers", "pytorch", "tensorflow"],
        DLTaskType.MACHINE_TRANSLATION: ["transformers", "pytorch"],
        DLTaskType.TIME_SERIES_FORECASTING: ["pytorch", "tensorflow", "keras"],
        DLTaskType.REINFORCEMENT_LEARNING: [
            "pytorch",
            "tensorflow",
            "stable_baselines3",
        ],
        DLTaskType.MULTIMODAL: ["transformers", "pytorch", "diffusers"],
    }

    def __init__(
        self,
        num_states: int = 256,
        num_actions: int = 20,
        alpha: float = 0.1,
        gamma: float = 0.95,
        epsilon: float = 1.0,
        epsilon_decay: float = 0.995,
        epsilon_min: float = 0.01,
        replay_capacity: int = 10000,
        target_update_freq: int = 100,
        tau: float = 0.005,
    ):
        self.q_table = DLQTable(
            num_states=num_states,
            num_actions=num_actions,
            alpha=alpha,
            gamma=gamma,
        )
        self.replay_buffer = ExperienceReplayBuffer(capacity=replay_capacity)
        self.epsilon = epsilon
        self.epsilon_decay = epsilon_decay
        self.epsilon_min = epsilon_min
        self.target_update_freq = target_update_freq
        self.tau = tau
        self.reward_history: list[float] = []
        self.selection_history: list[dict] = []
        self.training_steps = 0

    def compute_state(
        self,
        task_type: str,
        data_size: int,
        hardware: str,
        latency_budget: (float | None) = None,
        accuracy_budget: (float | None) = None,
    ) -> int:
        task_hash = hash(task_type) % 256
        size_factor = min(data_size / 1e6, 64) / 64
        hw_list = [
            HardwareType.CPU,
            HardwareType.NVIDIA_GPU,
            HardwareType.AMD_GPU,
            HardwareType.TPU,
            HardwareType.MULTI_GPU,
            HardwareType.CLUSTER,
        ]
        hw_idx = hw_list.index(hardware) if hardware in hw_list else 0
        hardware_factor = hw_idx / len(hw_list)
        state = int(
            (task_hash + size_factor * 100 + hardware_factor * 50)
            % self.q_table.num_states
        )
        return state

    def select_action(self, state: int, exploit: bool = False) -> int:
        return self.q_table.get_best_action(
            state, exploit=exploit, epsilon=self.epsilon
        )

    def update(
        self,
        state: int,
        action: int,
        reward: float,
        next_state: int,
        done: bool = False,
    ) -> float:
        td_error = self.q_table.update(state, action, reward, next_state, done)
        self.reward_history.append(reward)
        self.training_steps += 1
        if self.training_steps % self.target_update_freq == 0:
            self.q_table.update_target(self.tau)
        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay
        return td_error

    def add_experience(
        self,
        task_type: str,
        framework: str,
        reward: float,
        next_task_type: str,
        data_size: int,
        hardware: str,
        done: bool = False,
    ):
        state = self.compute_state(task_type, data_size, hardware)
        next_state = self.compute_state(next_task_type, data_size, hardware)

        all_frameworks = []
        for fw_list in self.TASK_FRAMEWORKS.values():
            all_frameworks.extend(fw_list)
        action_idx = (
            all_frameworks.index(framework) if framework in all_frameworks else 0
        )

        self.replay_buffer.push(
            state=state,
            action=action_idx,
            reward=reward,
            next_state=next_state,
            done=done,
            task_type=task_type,
            framework=framework,
            hardware=hardware,
        )

    def replay(self, batch_size: int = 32) -> float:
        if len(self.replay_buffer) < batch_size:
            return 0.0

        transitions, weights, indices = self.replay_buffer.sample(batch_size)
        weights = np.array(weights)
        total_loss = 0.0

        for i, transition in enumerate(transitions):
            td_error = self.q_table.update(
                transition.state,
                transition.action,
                transition.reward,
                transition.next_state,
                transition.done,
            )
            priority = abs(td_error) + 1e-6
            loss = weights[i] * priority**2
            total_loss += loss

        if len(indices) > 0:
            priorities = np.array(
                [
                    abs(self.q_table.q_table[t.state, t.action]) + 1e-6
                    for t in transitions
                ]
            )
            self.replay_buffer.update_priorities(list(indices), priorities)

        return total_loss / max(len(transitions), 1)

    def get_framework_scores(self, framework: str) -> dict[str, float]:
        return self.FRAMEWORK_CONFIGS.get(framework, {})

    def calculate_overall_score(
        self, framework: str, weights: (dict[str, float] | None) = None
    ) -> float:
        scores = self.get_framework_scores(framework)
        if not scores:
            return 0.0

        if weights is None:
            weights = {
                "training_speed": 0.20,
                "inference_speed": 0.15,
                "gpu_support": 0.15,
                "ease_of_use": 0.15,
                "community": 0.10,
                "pretrained_models": 0.10,
                "debugging": 0.08,
                "deployment": 0.07,
            }

        overall = sum(
            scores.get(criterion, 0.5) * weight for criterion, weight in weights.items()
        )
        return overall

    def select_best_framework(
        self,
        task_type: str,
        data_size: int,
        hardware: str,
        exploit: bool = True,
    ) -> dict[str, Any]:
        state = self.compute_state(task_type, data_size, hardware)
        action_idx = self.select_action(state, exploit=exploit)
        available_frameworks = self.TASK_FRAMEWORKS.get(task_type, [])
        if action_idx < len(available_frameworks):
            framework = available_frameworks[action_idx]
        else:
            framework = available_frameworks[0] if available_frameworks else "pytorch"

        q_value = (
            self.q_table.q_table[state, action_idx]
            if action_idx < self.q_table.num_actions
            else 0.0
        )
        scores = self.get_framework_scores(framework)
        overall = self.calculate_overall_score(framework)

        selection = {
            "framework": framework,
            "state": state,
            "q_value": q_value,
            "task_type": task_type,
            "hardware": hardware,
            "scores": scores,
            "overall_score": overall,
        }
        self.selection_history.append(selection)
        return selection

    def get_top_frameworks(
        self, task_type: str, top_k: int = 5
    ) -> list[dict[str, Any]]:
        available = self.TASK_FRAMEWORKS.get(task_type, [])
        frameworks = []
        for fw in available:
            overall = self.calculate_overall_score(fw)
            scores = self.get_framework_scores(fw)
            frameworks.append(
                {
                    "framework": fw,
                    "overall_score": overall,
                    "scores": scores,
                }
            )
        frameworks.sort(key=lambda x: x["overall_score"], reverse=True)
        return frameworks[:top_k]

    def save(self, path: str):
        self.q_table.save(path)

    def load(self, path: str):
        self.q_table.load(path)


def benchmark_framework(framework: str, task_type: str) -> float:
    return 0.5


if __name__ == "__main__":
    print("=== Deep Learning Q-Learning Demo ===\n")

    ql = DeepLearningQLearning()

    tasks = [
        (DLTaskType.IMAGE_CLASSIFICATION, 10000, HardwareType.NVIDIA_GPU),
        (DLTaskType.TEXT_CLASSIFICATION, 50000, HardwareType.CPU),
        (DLTaskType.IMAGE_GENERATION, 5000, HardwareType.NVIDIA_GPU),
    ]

    for task_type, data_size, hardware in tasks:
        result = ql.select_best_framework(task_type, data_size, hardware)
        print(f"Task: {task_type}")
        print(f"  Framework: {result['framework']}")
        print(f"  Q-Value: {result['q_value']:.3f}")
        print(f"  Overall: {result['overall_score']:.3f}")
        print()

    print("Top 3 Frameworks for NLP:")
    top = ql.get_top_frameworks(DLTaskType.TEXT_CLASSIFICATION, top_k=3)
    for fw in top:
        print(f"  {fw['framework']}: {fw['overall_score']:.3f}")

    print("\n=== Demo Complete ===")
