# coding: utf-8
"""
Bat Analysis

Module for scripts functionality.

Functions:
    - main()
    - to_dict()
    - parse_file()
    - parse_directory()
    - build_from_bat_files()
    - get_centrality()
    - get_dependencies()
    - get_interdependencies()
    - detect_cycles()
    - detect_orphans()

Classes:
    - TaskType
    - BatCommand
    - BatFile
    - BatParser
    - InteroperabilityGraph

Usage:
    >>> import bat_analysis
    >>> bat_analysis.main()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
BAT File Interoperability Analysis with Extended Q-Learning and Graph Visualization
Uses Deep Q-Learning with Tables to analyze .bat file dependencies and suggest improvements.
"""

import re
from pathlib import Path
# from typing import Dict, List, Set  # removed — all names have native equivalents
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum
import numpy as np
import matplotlib.pyplot as plt
import networkx as nx
from datetime import datetime
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class TaskType(Enum):
    """ML Task types for Q-Learning."""

    PARSING = "parsing"
    DEPENDENCY = "dependency"
    OPTIMIZATION = "optimization"
    VALIDATION = "validation"
    VISUALIZATION = "visualization"
    RECOMMENDATION = "recommendation"


@dataclass
class BatCommand:
    """Parsed command from .bat file."""

    name: str
    module: str
    parameters: list[str]
    line_number: int
    raw_line: str


@dataclass
class BatFile:
    """Parsed .bat file."""

    path: Path
    commands: list[BatCommand] = field(default_factory=list)
    modules_used: set[str] = field(default_factory=set)
    python_calls: set[str] = field(default_factory=set)
    raw_content: str = ""

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "commands": [c.name for c in self.commands],
            "modules": list(self.modules_used),
            "python_calls": list(self.python_calls),
        }


class BatParser:
    """Parser for .bat files."""

    PATTERNS = {
        "python_call": r"python\s+([^\s]+\.py)",
        "module_import": r"from\s+([\w.]+)\s+import",
        "module_call": r"(ml_platforms[\w\\/.]+)",
        "cd_command": r"cd\s+/d?\s+([^\n]+)",
        "param_pass": r"%\*|%\d",
    }

    MODULE_MAP = {
        "harness": "harness.py",
        "launcher": "launcher.py",
        "diagnostics": "diagnostics.py",
        "health_monitor": "health_monitor.py",
        "framework_activator": "framework_activator.py",
        "run_qanalysis": "run_qanalysis.py",
        "run_qanalysis_full": "run_qanalysis_full.py",
        "verify_moe": "verify_moe.py",
        "universal_converter": "universal_converter.py",
        "lm_studio_integration": "lm_studio_integration.py",
        "installation_debug": "installation_debug.py",
        "unified_analysis": "unified_analysis.py",
        "cross_platform_analysis": "cross_platform_analysis.py",
        "auto_install": "auto_install.py",
        "install_manager": "install_manager.py",
    }

    def parse_file(self, path: Path) -> BatFile:
        """Parse a single .bat file."""
        bat_file = BatFile(path=path)
        bat_file.raw_content = path.read_text(encoding="utf-8")

        lines = bat_file.raw_content.split("\n")
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith("REM") or line.startswith("::"):
                continue

            for match in re.finditer(self.PATTERNS["python_call"], line):
                py_file = match.group(1)
                bat_file.python_calls.add(py_file)
                bat_file.commands.append(
                    BatCommand(
                        name=Path(py_file).stem,
                        module=py_file,
                        parameters=[],
                        line_number=i,
                        raw_line=line,
                    )
                )

            for match in re.finditer(self.PATTERNS["module_call"], line):
                module = match.group(1)
                module_name = Path(module).stem
                bat_file.modules_used.add(module_name)
                bat_file.commands.append(
                    BatCommand(
                        name=module_name,
                        module=module,
                        parameters=self._extract_params(line),
                        line_number=i,
                        raw_line=line,
                    )
                )

        return bat_file

    def parse_directory(self, directory: Path) -> list[BatFile]:
        """Parse all .bat files in directory."""
        bat_files = []
        for bat_path in directory.rglob("*.bat"):
            try:
                bat_file = self.parse_file(bat_path)
                bat_files.append(bat_file)
            except Exception as e:
                print(f"Error parsing {bat_path}: {e}")
        return bat_files

    def _extract_params(self, line: str) -> list[str]:
        """Extract parameters from line."""
        params = []
        if "%*" in line:
            params.append("ALL_ARGS")
        param_matches = re.findall(r"%(\d)", line)
        for p in param_matches:
            params.append(f"ARG_{p}")
        return params


class InteroperabilityGraph:
    """Build and analyze interoperability graph between modules."""

    def __init__(self):
        self.graph = nx.DiGraph()
        self.edge_weights = defaultdict(lambda: {"calls": 0, "weight": 0})

    def build_from_bat_files(self, bat_files: list[BatFile]):
        """Build graph from parsed .bat files."""
        for bat_file in bat_files:
            source = bat_file.path.stem

            for command in bat_file.commands:
                target = command.name
                self.graph.add_node(source)
                self.graph.add_node(target)

                if self.graph.has_edge(source, target):
                    self.edge_weights[(source, target)]["calls"] += 1
                else:
                    self.edge_weights[(source, target)]["calls"] = 1

                self.graph.add_edge(source, target)

        for (src, tgt), data in self.edge_weights.items():
            data["weight"] = np.log1p(data["calls"])

    def get_centrality(self) -> dict[str, float]:
        """Calculate betweenness centrality."""
        return nx.betweenness_centrality(self.graph)

    def get_dependencies(self) -> dict[str, list[str]]:
        """Get dependencies for each module."""
        return {
            node: list(self.graph.predecessors(node)) for node in self.graph.nodes()
        }

    def get_interdependencies(self) -> dict[str, list[str]]:
        """Get modules that depend on each module."""
        return {node: list(self.graph.successors(node)) for node in self.graph.nodes()}

    def detect_cycles(self) -> list[list[str]]:
        """Detect cycles in the graph."""
        try:
            cycles = list(nx.simple_cycles(self.graph.to_undirected()))
            return [list(c) for c in cycles]
        except Exception as exc:
            note_failure("bat_analysis:L230", exc)
            return []

    def detect_orphans(self) -> list[str]:
        """Detect modules with no connections."""
        return [n for n in self.graph.nodes() if self.graph.degree(n) == 0]


class ExtendedQLearningAnalyzer:
    """Extended Q-Learning for optimizing .bat file interoperability."""

    def __init__(
        self,
        num_states: int = 100,
        num_actions: int = 20,
        alpha: float = 0.1,
        gamma: float = 0.95,
    ):
        self.q_table = np.zeros((num_states, num_actions))
        self.target_network = np.zeros((num_states, num_actions))
        self.replay_buffer: list[dict] = []
        self.reward_history: list[float] = []
        self.state_visits = np.zeros(num_states)
        self.training_steps = 0
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = 1.0
        self.epsilon_decay = 0.995
        self.epsilon_min = 0.01

        self.action_names = [
            "CONSOLIDATE",
            "SPLIT",
            "ADD_ERROR_HANDLING",
            "REMOVE_DUPLICATES",
            "ADD_LOGGING",
            "STANDARDIZE_PATHS",
            "ADD_HELP",
            "MERGE_MODULES",
            "EXTRACT_COMMON",
            "ADD_VALIDATION",
            "OPTIMIZE_IMPORTS",
            "ADD_COMMENTS",
            "REFACTOR_PARAMS",
            "ADD_VERBOSE",
            "CREATE_WRAPPER",
            "PARALLELIZE",
            "CACHE_RESULTS",
            "ADD_TIMEOUT",
            "IMPROVE_DOCS",
            "NO_OP",
        ]

    def state_from_bat(self, bat_file: BatFile) -> int:
        """Create state from .bat file features."""
        features = [
            len(bat_file.commands),
            len(bat_file.modules_used),
            len(bat_file.python_calls),
            len(bat_file.raw_content),
            sum(1 for c in bat_file.commands if "%" in c.raw_line),
        ]
        return hash(tuple(features)) % 100

    def get_available_actions(self, bat_file: BatFile) -> list[int]:
        """Get applicable actions for .bat file."""
        actions = []

        if len(bat_file.commands) > 10:
            actions.extend([0, 1])  # CONSOLIDATE, SPLIT

        if "echo" not in bat_file.raw_content.lower():
            actions.append(4)  # ADD_LOGGING

        if "pause" not in bat_file.raw_content.lower():
            actions.append(6)  # ADD_HELP

        if len(set(c.name for c in bat_file.commands)) < len(bat_file.commands):
            actions.append(3)  # REMOVE_DUPLICATES

        if bat_file.raw_content.count("cd /d") > 1:
            actions.append(5)  # STANDARDIZE_PATHS

        if len(bat_file.modules_used) > 3:
            actions.append(7)  # MERGE_MODULES

        if not any(
            "REM" in c.raw_line or "::" in c.raw_line for c in bat_file.commands
        ):
            actions.append(11)  # ADD_COMMENTS

        if not actions:
            actions = [19]  # NO_OP

        return actions

    def select_action(
        self, state: int, available_actions: list[int], exploit: bool = False
    ) -> int:
        """Select action using epsilon-greedy policy."""
        if not exploit and np.random.random() < self.epsilon:
            return np.random.choice(available_actions)

        q_values = [(a, self.q_table[state, a]) for a in available_actions]
        return max(q_values, key=lambda x: x[1])[0]

    def train(self, state: int, action: int, reward: float, next_state: int):
        """Train Q-Learning model."""
        current_q = self.q_table[state, action]
        best_next_q = max(self.q_table[next_state])

        td_target = reward + self.gamma * best_next_q
        td_error = td_target - current_q

        self.q_table[state, action] = current_q + self.alpha * td_error
        self.reward_history.append(reward)
        self.state_visits[state] += 1
        self.training_steps += 1

        if self.training_steps % 100 == 0:
            self.target_network = self.q_table.copy()
            if self.epsilon > self.epsilon_min:
                self.epsilon *= self.epsilon_decay

        return td_error

    def replay(self, batch_size: int = 32) -> float:
        """Train on batch from replay buffer."""
        if len(self.replay_buffer) < batch_size:
            return 0.0

        batch = np.random.choice(len(self.replay_buffer), batch_size, replace=False)
        total_loss = 0.0

        for idx in batch:
            transition = self.replay_buffer[idx]
            loss = self.train(
                transition["state"],
                transition["action"],
                transition["reward"],
                transition["next_state"],
            )
            total_loss += abs(loss)

        return total_loss / batch_size

    def analyze_bat_files(self, bat_files: list[BatFile]) -> list[dict]:
        """Analyze all .bat files and generate recommendations."""
        results = []

        for bat_file in bat_files:
            state = self.state_from_bat(bat_file)
            available_actions = self.get_available_actions(bat_file)
            action = self.select_action(state, available_actions)

            reward = self._calculate_reward(bat_file, action)

            next_state = state
            self.replay_buffer.append(
                {
                    "state": state,
                    "action": action,
                    "reward": reward,
                    "next_state": next_state,
                }
            )

            self.train(state, action, reward, next_state)

            results.append(
                {
                    "file": str(bat_file.path),
                    "state": state,
                    "action": self.action_names[action],
                    "action_id": action,
                    "reward": reward,
                    "modules": list(bat_file.modules_used),
                    "python_calls": list(bat_file.python_calls),
                    "num_commands": len(bat_file.commands),
                }
            )

        return results

    def _calculate_reward(self, bat_file: BatFile, action: int) -> float:
        """Calculate reward for action."""
        reward = 0.0

        if action == 0:  # CONSOLIDATE
            reward = min(1.0, len(bat_file.commands) / 20.0)
        elif action == 1:  # SPLIT
            reward = min(1.0, len(bat_file.modules_used) / 10.0)
        elif action == 3:  # REMOVE_DUPLICATES
            reward = (
                0.5
                if len(set(c.name for c in bat_file.commands)) < len(bat_file.commands)
                else 0.0
            )
        elif action == 4:  # ADD_LOGGING
            reward = 0.3
        elif action == 6:  # ADD_HELP
            reward = 0.4
        elif action == 5:  # STANDARDIZE_PATHS
            reward = 0.3 if bat_file.raw_content.count("cd /d") > 1 else 0.0
        elif action == 11:  # ADD_COMMENTS
            reward = 0.2
        else:
            reward = 0.1

        return reward

    def get_best_actions_summary(self) -> dict[str, int]:
        """Get count of best actions by state."""
        summary = defaultdict(int)
        for state in range(self.q_table.shape[0]):
            best_action = int(np.argmax(self.q_table[state]))
            summary[self.action_names[best_action]] += 1
        return dict(summary)


class GraphVisualizer:
    """Visualize interoperability graph."""

    def __init__(self, graph: nx.DiGraph, edge_weights: dict):
        self.graph = graph
        self.edge_weights = edge_weights
        self.fig_size = (14, 10)

    def plot_interoperability(self, output_path: str = "interoperability_graph.png"):
        """Plot and save interoperability graph."""
        plt.figure(figsize=self.fig_size)

        pos = nx.spring_layout(self.graph, k=2, iterations=50, seed=42)

        node_sizes = [max(300, self.graph.degree(n) * 200) for n in self.graph.nodes()]
        node_colors = [self.graph.degree(n) for n in self.graph.nodes()]

        nx.draw_networkx_nodes(
            self.graph,
            pos,
            node_size=node_sizes,
            node_color=node_colors,
            cmap="YlOrRd",
            alpha=0.8,
        )

        nx.draw_networkx_edges(
            self.graph,
            pos,
            width=2,
            alpha=0.6,
            arrows=True,
            arrowsize=20,
            connectionstyle="arc3,rad=0.1",
        )

        nx.draw_networkx_labels(self.graph, pos, font_size=8, font_weight="bold")

        plt.title("BAT File Interoperability Graph", fontsize=16, fontweight="bold")
        plt.axis("off")
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close()
        print(f"Saved graph to: {output_path}")

    def plot_qlearning_heatmap(
        self, q_table: np.ndarray, output_path: str = "qlearning_heatmap.png"
    ):
        """Plot Q-Learning heatmap."""
        plt.figure(figsize=(16, 10))

        plt.imshow(q_table[:50].T, aspect="auto", cmap="viridis", origin="lower")
        plt.colorbar(label="Q-Value")
        plt.xlabel("State", fontsize=12)
        plt.ylabel("Action", fontsize=12)
        plt.title(
            "Q-Learning State-Action Values (First 50 States)",
            fontsize=14,
            fontweight="bold",
        )

        action_names_short = [
            "CONS",
            "SPLIT",
            "ERR",
            "DEDUP",
            "LOG",
            "PATH",
            "HELP",
            "MERGE",
            "EXTR",
            "VALID",
            "IMPS",
            "COMM",
            "PARAM",
            "VERB",
            "WRAP",
            "PAR",
            "CACHE",
            "TIME",
            "DOCS",
            "NOOP",
        ]

        plt.yticks(range(len(action_names_short)), action_names_short)
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close()
        print(f"Saved Q-Learning heatmap to: {output_path}")

    def plot_module_network(
        self, centrality: dict[str, float], output_path: str = "module_network.png"
    ):
        """Plot module importance network."""
        plt.figure(figsize=(14, 10))

        G = self.graph.to_undirected()
        pos = nx.spring_layout(G, k=2, iterations=50, seed=42)

        centrality_values = [centrality.get(n, 0) for n in G.nodes()]
        node_sizes = [max(300, c * 5000) for c in centrality_values]

        nx.draw_networkx_nodes(
            G,
            pos,
            node_size=node_sizes,
            node_color=centrality_values,
            cmap="RdYlGn",
            alpha=0.8,
        )
        nx.draw_networkx_edges(G, pos, alpha=0.4, width=1.5)
        nx.draw_networkx_labels(G, pos, font_size=9, font_weight="bold")

        plt.title(
            "Module Importance Network (By Centrality)", fontsize=14, fontweight="bold"
        )
        plt.axis("off")
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close()
        print(f"Saved module network to: {output_path}")


class ImprovementAnalyzer:
    """Analyze and suggest improvements for .bat files."""

    def __init__(self, bat_files: list[BatFile], qlearner: ExtendedQLearningAnalyzer):
        self.bat_files = bat_files
        self.qlearner = qlearner

    def analyze_common_issues(self) -> dict[str, list[str]]:
        """Find common issues across all .bat files."""
        issues = {
            "missing_help": [],
            "missing_error_handling": [],
            "inconsistent_paths": [],
            "duplicate_calls": [],
            "missing_logging": [],
        }

        for bat_file in self.bat_files:
            content = bat_file.raw_content.lower()

            if "/?" not in content and "help" not in content:
                issues["missing_help"].append(str(bat_file.path))

            if "echo" not in content:
                issues["missing_logging"].append(str(bat_file.path))

            if "if errorlevel" not in content and "||" not in content:
                issues["missing_error_handling"].append(str(bat_file.path))

            if content.count("cd /d") > 1:
                issues["inconsistent_paths"].append(str(bat_file.path))

        return issues

    def suggest_improvements(self) -> list[dict]:
        """Generate improvement suggestions."""
        suggestions = []

        issues = self.analyze_common_issues()

        for bat_file in self.bat_files:
            state = self.qlearner.state_from_bat(bat_file)
            available_actions = self.qlearner.get_available_actions(bat_file)
            action = self.qlearner.select_action(state, available_actions, exploit=True)
            action_name = self.qlearner.action_names[action]

            file_suggestions = {
                "file": str(bat_file.path),
                "priority": "HIGH" if len(bat_file.commands) > 15 else "MEDIUM",
                "issues": [],
                "suggested_action": action_name,
                "improvements": [],
            }

            if str(bat_file.path) in issues["missing_help"]:
                file_suggestions["issues"].append("Missing help flag (/?)")
                file_suggestions["improvements"].append(
                    "Add /? help flag to all commands"
                )

            if str(bat_file.path) in issues["missing_logging"]:
                file_suggestions["issues"].append("No logging/echo statements")
                file_suggestions["improvements"].append(
                    "Add echo statements for progress tracking"
                )

            if str(bat_file.path) in issues["missing_error_handling"]:
                file_suggestions["issues"].append("No error handling")
                file_suggestions["improvements"].append(
                    "Add || echo Error occurred after commands"
                )

            if str(bat_file.path) in issues["inconsistent_paths"]:
                file_suggestions["issues"].append("Multiple cd /d commands")
                file_suggestions["improvements"].append(
                    "Consolidate path changes or use batch files"
                )

            if action_name == "CONSOLIDATE":
                file_suggestions["improvements"].append(
                    "Consider combining multiple commands"
                )
            elif action_name == "ADD_LOGGING":
                file_suggestions["improvements"].append(
                    "Add @echo on and progress messages"
                )
            elif action_name == "ADD_HELP":
                file_suggestions["improvements"].append("Add usage information at top")
            elif action_name == "REMOVE_DUPLICATES":
                file_suggestions["improvements"].append(
                    "Deduplicate repeated command calls"
                )

            if file_suggestions["issues"] or file_suggestions["improvements"]:
                suggestions.append(file_suggestions)

        return sorted(suggestions, key=lambda x: 0 if x["priority"] == "HIGH" else 1)

    def generate_optimized_template(self) -> str:
        """Generate an optimized .bat template."""
        template = """@echo off
REM ============================================================================
REM Optimized Batch File Template
REM Generated by Extended Q-Learning Analyzer
REM ============================================================================

setlocal EnableDelayedExpansion

REM Configuration
set "BASE_DIR=%USERPROFILE%\\Desktop\\Azazael"
set "PYTHON_CMD=python"
set "LOG_FILE=logs\\%DATE:~-4,4%-%DATE:~-10,2%-%DATE:~-7,2%_exec.log"

REM Create log directory if not exists
if not exist logs mkdir logs

REM ============================================================================
REM Main Execution
REM ============================================================================

echo [%TIME%] Starting execution... >> "%LOG_FILE%"

cd /d "%BASE_DIR%"

REM Validate environment
if not exist "%BASE_DIR%" (
    echo ERROR: Base directory not found!
    echo Base directory: "%BASE_DIR%"
    goto :ERROR
)

REM Your commands here
REM ============================================================================
"""

        return template

    def generate_improved_bat(self, bat_file: BatFile) -> str:
        """Generate improved version of a .bat file."""
        lines = bat_file.raw_content.split("\n")
        improved = []

        improved.append("@echo off")
        improved.append("REM Improved by Extended Q-Learning Analyzer")
        improved.append(f"REM Original: {bat_file.path.name}")
        improved.append(f"REM Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        improved.append("")

        for line in lines:
            stripped = line.strip()

            if not stripped or stripped.startswith("REM") or stripped.startswith("::"):
                if stripped and not stripped.startswith("REM Improved"):
                    improved.append(line)
                continue

            improved.append(line)

            if "python" in stripped.lower() and "ml_platforms" in stripped.lower():
                improved.append(
                    "if %ERRORLEVEL% neq 0 echo Error running command & goto :EOF"
                )

        return "\n".join(improved)


def main():
    """Main analysis pipeline."""
    print("=" * 70)
    print("BAT FILE INTEROPERABILITY ANALYSIS")
    print("Extended Q-Learning with Graph Visualization")
    print("=" * 70)

    base_path = Path.home() / "Desktop" / "SHORTY"

    print("\n[1] Parsing BAT files...")
    parser = BatParser()
    bat_files = parser.parse_directory(base_path)
    print(f"    Parsed {len(bat_files)} BAT files")

    for bf in bat_files:
        print(
        f"    - {bf.path.name}: {len(bf.commands)} commands, {len(bf.modules_used)} modules"
        )

        print("\n[2] Building interoperability graph...")
        graph_builder = InteroperabilityGraph()
        graph_builder.build_from_bat_files(bat_files)
        print(f"    Nodes: {graph_builder.graph.number_of_nodes()}")
        print(f"    Edges: {graph_builder.graph.number_of_edges()}")

        centrality = graph_builder.get_centrality()
        max_centrality_node = max(centrality, key=centrality.__getitem__)
        max_centrality_value = max(list(centrality.values()))
        print(f"    Most central: {max_centrality_node} ({max_centrality_value:.3f})")

        cycles = graph_builder.detect_cycles()
        orphans = graph_builder.detect_orphans()
        print(f"    Cycles detected: {len(cycles)}")
        print(f"    Orphan nodes: {len(orphans)}")

        print("\n[3] Training Extended Q-Learning model...")
        qlearner = ExtendedQLearningAnalyzer(num_states=100, num_actions=20)
        qlearner.analyze_bat_files(bat_files)

        print(f"    Training steps: {qlearner.training_steps}")
        print(f"    Final epsilon: {qlearner.epsilon:.3f}")
        print(f"    Average reward: {np.mean(qlearner.reward_history):.3f}")

        print("\n[4] Generating visualizations...")
        visualizer = GraphVisualizer(graph_builder.graph, graph_builder.edge_weights)
        visualizer.plot_interoperability("interoperability_graph.png")
        visualizer.plot_qlearning_heatmap(qlearner.q_table, "qlearning_heatmap.png")
        visualizer.plot_module_network(centrality, "module_network.png")

        print("\n[5] Analyzing improvements...")
        analyzer = ImprovementAnalyzer(bat_files, qlearner)
        suggestions = analyzer.suggest_improvements()

        print(f"    Found {len(suggestions)} improvement suggestions")

        print("\n[6] Generating report...")
        report = [
        "# BAT File Interoperability Analysis Report",
        "=" * 50,
        "",
        f"Date: {datetime.now()}",
        "",
        "## Summary",
        "- Parsed BAT files: {len(bat_files)}",
        f"- Graph nodes: {graph_builder.graph.number_of_nodes()}",
        f"- Graph edges: {graph_builder.graph.number_of_edges()}",
        f"- Q-Learning training steps: {qlearner.training_steps}",
        "",
        "## Best Actions by State (Q-Learning)",
        "",
        ]

        action_summary = qlearner.get_best_actions_summary()
        for action, count in sorted(action_summary.items(), key=lambda x: -x[1]):
            report.append(f"- {action}: {count} states")

            report.extend(["", "## Improvement Suggestions", ""])
            for sug in suggestions[:10]:
                report.append(f"### {sug['file']}")
                report.append(f"- Priority: {sug['priority']}")
                report.append(f"- Suggested Action: {sug['suggested_action']}")
                for issue in sug["issues"]:
                    report.append(f"  - Issue: {issue}")
                    for imp in sug["improvements"]:
                        report.append(f"  - Fix: {imp}")
                        report.append("")

                        report_text = "\n".join(report)
                        Path("BAT_ANALYSIS_REPORT.md").write_text(report_text, encoding="utf-8")
                        print("    Report saved to: BAT_ANALYSIS_REPORT.md")

                        print("\n[7] Generating optimized template...")
                        template = analyzer.generate_optimized_template()
                        Path("OPTIMIZED_BAT_TEMPLATE.bat").write_text(template, encoding="utf-8")
                        print("    Template saved to: OPTIMIZED_BAT_TEMPLATE.bat")

                        print("\n" + "=" * 70)
                        print("ANALYSIS COMPLETE")
                        print("=" * 70)
                        print("\nGenerated files:")
                        print("  - interoperability_graph.png")
                        print("  - qlearning_heatmap.png")
                        print("  - module_network.png")
                        print("  - BAT_ANALYSIS_REPORT.md")
                        print("  - OPTIMIZED_BAT_TEMPLATE.bat")

                        return bat_files, qlearner, graph_builder


if __name__ == "__main__":
    main()
