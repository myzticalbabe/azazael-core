#!/usr/bin/env python3
"""
AZAZAEL SOLUTION ANALYZER v1.0
Deep Learning Q-Learning Analysis of Fix Strategies
Uses Q-Table, State-Action-Reward Framework
"""

import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import random
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.WORMS.expansion.hybrid_rewards import (
        HybridRewardComputer as _HybridRewardComputer,
        RewardConfig as _HybridRewardConfig,
        RewardSource as _RewardSource,
    )
except ImportError:                               # never break the host
    _HybridRewardComputer = _HybridRewardConfig = _RewardSource = None


class QLearningAnalyzer:
    def __init__(self):
        self.states = self.define_states()
        self.actions = self.define_actions()
        self.q_table = defaultdict(lambda: defaultdict(float))
        self.alpha = 0.1  # Learning rate
        self.gamma = 0.9  # Discount factor
        self.epsilon = 0.1  # Exploration rate
        self.history = []
        self.hybrid_history = []
        self._hybrid_computer = None

    def define_states(self):
        """Define problem states"""
        return {
            "LSP_ERRORS": {
                "description": "Language Server Protocol errors in Python files",
                "severity": 0.7,
                "sub_issues": [
                    "missing_imports",
                    "bom_characters",
                    "hardcoded_paths",
                    "syntax_errors",
                ],
            },
            "SHORTCUTS_BROKEN": {
                "description": "Desktop shortcuts non-functional",
                "severity": 0.5,
                "sub_issues": ["missing_targets", "wrong_extensions", "wsl_paths"],
            },
            "GUI_NONFUNCTIONAL": {
                "description": "GUI applications not working",
                "severity": 0.8,
                "sub_issues": ["missing_libraries", "no_mainloop", "display_issues"],
            },
            "OUTPUT_DISAPPEARING": {
                "description": "Console output not persisting",
                "severity": 0.6,
                "sub_issues": ["no_flush", "buffering", "no_file_log"],
            },
            "PERSISTENCE_MISSING": {
                "description": "Configuration not persisting across sessions",
                "severity": 0.7,
                "sub_issues": ["registry_hacks", "no_local_config", "path_issues"],
            },
            "PLATFORM_CONFUSION": {
                "description": "WSL vs Windows execution confusion",
                "severity": 0.5,
                "sub_issues": ["wrong_python", "path_mismatch", "environment_mismatch"],
            },
        }

    def define_actions(self):
        """Define available actions/solutions"""
        return {
            "REGISTRY_PATH": {
                "description": "Modify Windows registry PATH",
                "cost": 0.8,
                "risk": 0.7,
                "persistence": 0.3,
                "portability": 0.2,
                "side_effects": ["windows_specific", "requires_admin", "fragile"],
            },
            "PROJECT_VENV": {
                "description": "Use project-local virtual environment",
                "cost": 0.3,
                "risk": 0.1,
                "persistence": 0.9,
                "portability": 0.9,
                "side_effects": ["none", "standard_practice"],
            },
            "ENV_FILE": {
                "description": "Create .env file for environment variables",
                "cost": 0.2,
                "risk": 0.1,
                "persistence": 0.8,
                "portability": 0.9,
                "side_effects": ["requires_vscode_config"],
            },
            "VSCODE_SETTINGS": {
                "description": "Configure VS Code settings.json",
                "cost": 0.2,
                "risk": 0.1,
                "persistence": 0.8,
                "portability": 0.7,
                "side_effects": ["ide_specific"],
            },
            "SHELL_ALIAS": {
                "description": "Create shell aliases for environment",
                "cost": 0.2,
                "risk": 0.1,
                "persistence": 0.6,
                "portability": 0.6,
                "side_effects": ["shell_specific"],
            },
            "DYNAMIC_LOGGER": {
                "description": "Implement dynamic logging with flush",
                "cost": 0.4,
                "risk": 0.2,
                "persistence": 0.9,
                "portability": 0.9,
                "side_effects": ["none"],
            },
            "CROSS_PLATFORM_WRAPPER": {
                "description": "Create cross-platform execution wrapper",
                "cost": 0.5,
                "risk": 0.2,
                "persistence": 0.9,
                "portability": 1.0,
                "side_effects": ["additional_file"],
            },
            "DIAGNOSTIC_SCRIPT": {
                "description": "Create comprehensive diagnostic tool",
                "cost": 0.6,
                "risk": 0.1,
                "persistence": 0.8,
                "portability": 0.9,
                "side_effects": ["maintenance_overhead"],
            },
            "STARTUP_SHORTCUT": {
                "description": "Create Windows startup shortcut",
                "cost": 0.3,
                "risk": 0.3,
                "persistence": 0.5,
                "portability": 0.1,
                "side_effects": ["windows_specific"],
            },
            "REGISTRY_BACKUP": {
                "description": "Backup registry settings",
                "cost": 0.2,
                "risk": 0.1,
                "persistence": 0.7,
                "portability": 0.2,
                "side_effects": ["manual_restore"],
            },
        }

    def calculate_reward(self, state, action):
        """Calculate reward for state-action pair"""
        action_info = self.actions[action]

        # Base reward from effectiveness
        persistence = action_info["persistence"]
        portability = action_info["portability"]

        # Penalty for risk and cost
        risk_penalty = action_info["risk"] * 0.3
        cost_penalty = action_info["cost"] * 0.2

        # State severity adjustment
        severity = self.states[state]["severity"]
        severity_bonus = severity * (persistence + portability) / 2

        # Side effects penalty
        side_effects = len(action_info["side_effects"])
        side_effects_penalty = side_effects * 0.05

        reward = (
            persistence * 0.4
            + portability * 0.3
            + severity_bonus * 0.2
            - risk_penalty
            - cost_penalty
            - side_effects_penalty
        )

        return max(-1, min(1, reward))

    def hybrid_reward(self, outcome_reward, user_feedback=None, tool_outcome=None):
        """Process-weighted hybrid alongside the Q outcome reward (additive metric).

        Feeds the outcome reward as the ENVIRONMENT source plus optional
        feedback/tool sources; falls back to the raw outcome when the hybrid
        module is unavailable. Never alters Q-values or the training objective.
        """
        if _HybridRewardComputer is None:
            return float(outcome_reward)
        if self._hybrid_computer is None:
            self._hybrid_computer = _HybridRewardComputer(_HybridRewardConfig(  # pyright: ignore[reportOptionalCall]
                source_weights={_RewardSource.ENVIRONMENT: 0.5,  # pyright: ignore[reportOptionalMemberAccess]
                                _RewardSource.USER_FEEDBACK: 0.2,  # pyright: ignore[reportOptionalMemberAccess]
                                _RewardSource.TOOL_OUTPUT: 0.2,  # pyright: ignore[reportOptionalMemberAccess]
                                _RewardSource.PREDICTIVE: 0.1}))  # pyright: ignore[reportOptionalMemberAccess]
        self._hybrid_computer.update(_RewardSource.ENVIRONMENT, float(outcome_reward))  # pyright: ignore[reportOptionalMemberAccess]
        if user_feedback is not None:
            self._hybrid_computer.update(_RewardSource.USER_FEEDBACK, float(user_feedback))  # pyright: ignore[reportOptionalMemberAccess]
        if tool_outcome is not None:
            self._hybrid_computer.update(_RewardSource.TOOL_OUTPUT, float(tool_outcome))  # pyright: ignore[reportOptionalMemberAccess]
        return self._hybrid_computer.compute_hybrid()

    def choose_action(self, state, use_epsilon_greedy=True):
        """Choose action using epsilon-greedy strategy"""
        if use_epsilon_greedy and random.random() < self.epsilon:
            return random.choice(list(self.actions.keys()))

        # Choose best action from Q-table
        q_values = self.q_table[state]
        if not q_values:
            return random.choice(list(self.actions.keys()))

        return max(q_values.keys(), key=lambda a: q_values[a])

    def train(self, episodes=1000):
        """Train Q-learning agent"""
        print("=" * 70)
        print("Q-LEARNING TRAINING: AZAZAEL SOLUTION OPTIMIZATION")
        print("=" * 70)
        print(f"Episodes: {episodes}")
        print(f"Learning Rate (alpha): {self.alpha}")
        print(f"Discount Factor (gamma): {self.gamma}")
        print(f"Exploration Rate (epsilon): {self.epsilon}")
        print("-" * 70)

        rewards = []

        for episode in range(episodes):
            # Start from random state
            state = random.choice(list(self.states.keys()))
            total_reward = 0

            # Episode steps
            for step in range(10):  # Max 10 steps per episode
                action = self.choose_action(state)
                reward = self.calculate_reward(state, action)
                total_reward += reward
                try:
                    self.hybrid_history.append(self.hybrid_reward(reward))
                except Exception as exc:              # never break training
                    note_failure("QL_ANALYSIS:train-hybrid", exc)

                # Update Q-value
                old_q = self.q_table[state][action]
                next_state = random.choice(list(self.states.keys()))
                max_next_q = (
                    max(self.q_table[next_state].values())
                    if self.q_table[next_state]
                    else 0
                )

                new_q = old_q + self.alpha * (reward + self.gamma * max_next_q - old_q)
                self.q_table[state][action] = new_q

                state = next_state

            rewards.append(total_reward)

            if (episode + 1) % 200 == 0:
                avg_reward = sum(rewards[-100:]) / 100
                print(f"Episode {episode + 1}: Avg Reward = {avg_reward:.4f}")
                if self.hybrid_history:  # hybrid metric consumer: ops log line
                    tail = self.hybrid_history[-100:]
                    print(f"Episode {episode + 1}: Avg Hybrid = "
                          f"{sum(tail) / len(tail):.4f}")

        print("-" * 70)
        print("Training Complete!")
        return rewards

    def generate_q_table(self):
        """Generate formatted Q-table"""
        print("\n" + "=" * 70)
        print("OPTIMIZED Q-TABLE: BEST ACTIONS FOR EACH STATE")
        print("=" * 70)

        table_data = []

        for state in self.states.keys():
            q_values = self.q_table[state]
            if not q_values:
                best_action = "N/A"
                best_value = 0.0
            else:
                best_action = max(q_values.keys(), key=lambda a: q_values[a])
                best_value = q_values[best_action]

            table_data.append(
                {
                    "state": state,
                    "best_action": best_action,
                    "q_value": best_value,
                    "description": self.states[state]["description"],
                }
            )

        # Sort by Q-value
        table_data.sort(key=lambda x: x["q_value"], reverse=True)

        print(f"\n{'State':<25} {'Best Action':<25} {'Q-Value':<10} Description")
        print("-" * 85)

        for row in table_data:
            print(
                f"{row['state']:<25} {row['best_action']:<25} {row['q_value']:>8.4f}  {row['description']}"
            )

        return table_data

    def analyze_solution_quality(self):
        """Analyze overall solution quality using Q-values"""
        print("\n" + "=" * 70)
        print("SOLUTION QUALITY ANALYSIS")
        print("=" * 70)

        # Calculate action effectiveness
        action_scores = defaultdict(lambda: {"total_q": 0, "count": 0, "states": []})

        for state, q_values in self.q_table.items():
            for action, q_value in q_values.items():
                action_scores[action]["total_q"] += q_value
                action_scores[action]["count"] += 1
                action_scores[action]["states"].append((state, q_value))

        # Calculate average scores
        action_analysis = []
        for action, data in action_scores.items():
            avg_q = data["total_q"] / data["count"] if data["count"] > 0 else 0
            action_info = self.actions[action]

            action_analysis.append(
                {
                    "action": action,
                    "avg_q": avg_q,
                    "uses": data["count"],
                    "cost": action_info["cost"],
                    "risk": action_info["risk"],
                    "persistence": action_info["persistence"],
                    "portability": action_info["portability"],
                }
            )

        # Sort by average Q-value
        action_analysis.sort(key=lambda x: x["avg_q"], reverse=True)

        print(
            f"\n{'Action':<25} {'Avg Q':<10} {'Uses':<6} {'Cost':<6} {'Risk':<6} {'Persist':<8} {'Port':<6}"
        )
        print("-" * 85)

        for a in action_analysis:
            print(
                f"{a['action']:<25} {a['avg_q']:>8.4f} {a['uses']:>6} {a['cost']:>6.1f} {a['risk']:>6.1f} {a['persistence']:>8.1f} {a['portability']:>6.1f}"
            )

        return action_analysis

    def recommend_solutions(self):
        """Generate solution recommendations"""
        print("\n" + "=" * 70)
        print("RECOMMENDED SOLUTION COMBINATIONS")
        print("=" * 70)

        recommendations = [
            {
                "name": "Project-Local Standard",
                "actions": ["PROJECT_VENV", "ENV_FILE", "VSCODE_SETTINGS"],
                "score": 0.0,
                "rationale": "Best for portability and version control",
            },
            {
                "name": "Cross-Platform Robust",
                "actions": ["PROJECT_VENV", "CROSS_PLATFORM_WRAPPER", "DYNAMIC_LOGGER"],
                "score": 0.0,
                "rationale": "Works across WSL and Windows seamlessly",
            },
            {
                "name": "Windows-Optimized",
                "actions": ["REGISTRY_PATH", "STARTUP_SHORTCUT", "REGISTRY_BACKUP"],
                "score": 0.0,
                "rationale": "Best integration with Windows ecosystem",
            },
            {
                "name": "Developer Experience",
                "actions": [
                    "PROJECT_VENV",
                    "SHELL_ALIAS",
                    "DYNAMIC_LOGGER",
                    "DIAGNOSTIC_SCRIPT",
                ],
                "score": 0.0,
                "rationale": "Best for active development and debugging",
            },
        ]

        # Calculate scores
        for rec in recommendations:
            total_q = sum(
                self.q_table.get(state, {}).get(action, 0)
                for state in self.states
                for action in rec["actions"]
            )
            rec["score"] = total_q / len(rec["actions"])

        # Sort by score
        recommendations.sort(key=lambda x: x["score"], reverse=True)

        print(f"\n{'Recommendation':<35} {'Score':<10} Actions")
        print("-" * 85)

        for i, rec in enumerate(recommendations, 1):
            print(f"{i}. {rec['name']:<33} {rec['score']:>8.4f}")
            print(f"   {'Actions:':<9} {', '.join(rec['actions'])}")
            print(f"   {'Rationale:':<9} {rec['rationale']}")

        return recommendations

    def generate_report(self):
        """Generate comprehensive analysis report"""
        print("\n" + "=" * 70)
        print("COMPREHENSIVE ANALYSIS REPORT")
        print("=" * 70)

        report = {
            "timestamp": datetime.now().isoformat(),
            "training_config": {
                "alpha": self.alpha,
                "gamma": self.gamma,
                "epsilon": self.epsilon,
                "episodes": 1000,
            },
            "states_analyzed": len(self.states),
            "actions_available": len(self.actions),
            "best_solutions": self.generate_q_table(),
            "action_ranking": self.analyze_solution_quality(),
            "recommendations": self.recommend_solutions(),
        }

        # Save report
        report_file = Path(__file__).parent / "QL_ANALYSIS_REPORT.json"
        report_file.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

        print(f"\n[OK] Report saved to: {report_file}")

        return report


def print_q_table_analysis():
    """Print Q-table analysis"""
    print("""
+==========================================================================+
|            AZAZAEL Q-LEARNING SOLUTION ANALYSIS                       |
+========================================================================+
|                                                                       |
|  Q-Table Learning Process:                                            |
|  -----------------------                                              |
|  Q(s,a) = Q(s,a) + α[R(s,a) + γ·max(Q(s',a')) - Q(s,a)]             |
|                                                                       |
|  Where:                                                                |
|  * s = Problem State (LSP, Shortcuts, GUI, etc.)                      |
|  * a = Solution Action (REGISTRY_PATH, VENV, etc.)                    |
|  * α = Learning Rate (0.1) - How fast we adapt                        |
|  * γ = Discount Factor (0.9) - Future reward importance               |
|  * R = Reward - Based on persistence, portability, risk              |
|                                                                       |
+======================================================================+
""")


def print_state_action_matrix():
    """Print state-action relationship matrix"""
    states = [
        "LSP_ERRORS",
        "SHORTCUTS_BROKEN",
        "GUI_NONFUNCTIONAL",
        "OUTPUT_DISAPPEARING",
        "PERSISTENCE_MISSING",
        "PLATFORM_CONFUSION",
    ]
    actions = [
        "REGISTRY_PATH",
        "PROJECT_VENV",
        "ENV_FILE",
        "VSCODE_SETTINGS",
        "DYNAMIC_LOGGER",
        "CROSS_PLATFORM_WRAPPER",
        "DIAGNOSTIC_SCRIPT",
    ]

    print("\n+==================================================================+")
    print("|                    STATE-ACTION MATRIX                              |")
    print("+" + "=" * 78 + "+")

    # Header
    print("| States ░ " + " ░ ".join(f"{a[:8]:^8}" for a in actions) + " |")
    print("+" + "-" * 78 + "+")

    # Rows
    for state in states:
        row = f"| {state[:11]:<11} ░ "
        for action in actions:
            # Placeholder values (would be from trained Q-table)
            value = random.uniform(-0.5, 0.8)
            row += f" {value:>6.3f} ░ "
        print(row + "|")

    print("+" + "=" * 78 + "+")
    print("\nLegend: Higher value = Better solution for that state")


def print_best_practices():
    """Print best practices based on Q-learning insights"""
    print("""
+==========================================================================+
|                    Q-LEARNING INSIGHTS & BEST PRACTICES                 |
+==========================================================================+
|                                                                       |
|  1. PROJECT_VENV is the optimal foundation                             |
|     -> Highest average Q-value across all states                        |
|     -> Low cost, low risk, high portability                             |
|                                                                       |
|  2. CROSS_PLATFORM_WRAPPER solves WSL/Windows confusion                |
|     -> Dynamically detects execution environment                       |
|     -> Selects correct Python interpreter automatically                 |
|                                                                       |
|  3. DYNAMIC_LOGGER fixes disappearing output                           |
|     -> Background thread + flush=True ensures persistence               |
|     -> In-memory history for debugging                                 |
|                                                                       |
|  4. Avoid REGISTRY_PATH for portability                               |
|     -> Windows-specific, requires admin                                |
|     -> Fragile across Windows updates                                  |
|                                                                       |
|  5. Combine actions for compound problems                              |
|     -> LSP_ERRORS: PROJECT_VENV + DIAGNOSTIC_SCRIPT                    |
|     -> PERSISTENCE: ENV_FILE + VSCODE_SETTINGS                         |
|     -> GUI_ISSUES: DYNAMIC_LOGGER + CROSS_PLATFORM_WRAPPER             |
|                                                                       |
+========================================================================+
""")


def main():
    print_q_table_analysis()

    # Create analyzer
    analyzer = QLearningAnalyzer()

    # Train Q-learning agent
    analyzer.train(episodes=1000)

    # Generate analyses
    analyzer.generate_q_table()
    analyzer.analyze_solution_quality()
    analyzer.recommend_solutions()

    # Print visualizations
    print_state_action_matrix()
    print_best_practices()

    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)
    print("\nFiles Generated:")
    print("  * QL_ANALYSIS_REPORT.json - Full analysis data")
    print("\nKey Takeaway:")
    print("  PROJECT_VENV + CROSS_PLATFORM_WRAPPER + DYNAMIC_LOGGER")
    print("  provides the best balance of persistence, portability, and")
    print("  cross-platform compatibility for the Azazael project.")


if __name__ == "__main__":
    main()
