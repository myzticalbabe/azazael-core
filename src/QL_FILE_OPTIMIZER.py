# coding: utf-8
"""
QLearning File Optimizer
Extended Q-Learning with Tables for Desktop File Deletion Decisions
Uses ML frameworks to analyze and recommend file cleanup actions
"""

import json
import numpy as np
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, List
from collections import defaultdict
import random

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

DESKTOP_PATH = Path.home() / "Desktop"
OUTPUT_PATH = DESKTOP_PATH / "CODEX"


@dataclass
class FileFeatures:
    """Extracted features for a file."""

    name: str
    path: str
    size_bytes: int
    modified_days_ago: int
    is_shortcut: bool
    is_text: bool
    is_code: bool
    is_doc: bool
    ecosystem_related: bool
    last_access_days: int
    extension: str
    read_access_count: int = 0
    write_access_count: int = 0

    def to_vector(self) -> np.ndarray:
        """Convert to feature vector for Q-learning."""
        return np.array(
            [
                np.log1p(self.size_bytes) / 20,
                min(self.modified_days_ago / 365, 1),
                int(self.is_shortcut),
                int(self.is_text),
                int(self.is_code),
                int(self.is_doc),
                int(self.ecosystem_related),
                min(self.last_access_days / 365, 1),
            ]
        )


@dataclass
class QTableEntry:
    """Q-Table entry for a state-action pair."""

    state_hash: str
    action: str
    q_value: float
    visit_count: int = 0
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())
    reward_history: list[float] = field(default_factory=list)


class QLearningFileOptimizer:
    """
    Q-Learning agent for file deletion optimization.
    Uses table-based Q-learning with feature-based state representation.
    """

    def __init__(
        self,
        learning_rate: float = 0.1,
        discount_factor: float = 0.95,
        exploration_rate: float = 1.0,
        exploration_decay: float = 0.995,
        min_exploration: float = 0.01,
    ):
        self.lr = learning_rate
        self.gamma = discount_factor
        self.epsilon = exploration_rate
        self.epsilon_decay = exploration_decay
        self.epsilon_min = min_exploration

        self.q_table: dict[str, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        self.experience_buffer: list[dict] = []
        self.state_size = 8
        self.action_size = 3
        self.actions = ["KEEP", "DELETE", "REVIEW"]

    def get_state_key(self, features: FileFeatures) -> str:
        """Convert features to discretized state key."""
        vector = features.to_vector()
        discretized = []
        for val in vector:
            if val < 0.2:
                discretized.append("L")
            elif val < 0.5:
                discretized.append("M")
            elif val < 0.8:
                pass
            else:
                discretized.append("X")
        discretized.append(
            features.extension[:3].upper() if features.extension else "NON"
        )
        return "|".join(discretized)

    def choose_action(self, state: str, training: bool = True) -> str:
        """Choose action using epsilon-greedy policy."""
        if training and random.random() < self.epsilon:
            return random.choice(self.actions)
        q_values = self.q_table[state]
        if not q_values:
            return random.choice(self.actions)
        return max(q_values, key=q_values.__getitem__)

    def update_q_table(
        self, state: str, action: str, reward: float, next_state: str, done: bool
    ):
        """Update Q-value using Bellman equation."""
        current_q = self.q_table[state][action]
        next_vals = self.q_table[next_state].values()
        next_max_q = max(next_vals) if not done and next_vals else 0
        new_q = current_q + self.lr * (reward + self.gamma * next_max_q - current_q)
        self.q_table[state][action] = new_q

        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay

    def get_action_reward(self, features: FileFeatures, action: str) -> float:
        """Calculate reward for an action based on file features."""
        reward = 0.0

        if action == "KEEP":
            if features.ecosystem_related:
                reward = 1.0
            elif features.is_shortcut or features.is_doc:
                reward = 0.5
            elif features.modified_days_ago < 30:
                reward = 0.8
            else:
                reward = 0.3

        elif action == "DELETE":
            if features.is_shortcut and not features.ecosystem_related:
                reward = 0.9
            elif features.modified_days_ago > 180 and not features.ecosystem_related:
                reward = 0.7
            elif features.size_bytes > 1_000_000 and not features.ecosystem_related:
                reward = 0.5
            else:
                reward = -1.0

        elif action == "REVIEW":
            reward = 0.0

        return reward

    def train(self, files: list[FileFeatures], episodes: int = 100):
        """Train Q-learning agent on file dataset."""
        print(
            f"Training Q-Learning agent on {len(files)} files for {episodes} episodes..."
        )

        for episode in range(episodes):
            random.shuffle(files)
            total_reward = 0

            for features in files:
                state = self.get_state_key(features)
                action = self.choose_action(state)
                reward = self.get_action_reward(features, action)
                total_reward += reward

                next_features = random.choice(files)
                next_state = self.get_state_key(next_features)
                done = random.random() < 0.1

                self.update_q_table(state, action, reward, next_state, done)

            if (episode + 1) % 20 == 0:
                avg_reward = total_reward / len(files)
                print(
                    f"  Episode {episode + 1}/{episodes} | Avg Reward: {avg_reward:.3f} | Epsilon: {self.epsilon:.3f}"
                )

    def evaluate(self, files: list[FileFeatures]) -> dict[str, Any]:
        """Evaluate trained agent and get recommendations."""
        recommendations = {"KEEP": [], "DELETE": [], "REVIEW": []}

        for features in files:
            state = self.get_state_key(features)
            action = self.choose_action(state, training=False)
            q_values = self.q_table[state]

            confidence = max(q_values.values()) if q_values else 0.0

            recommendations[action].append(
                {
                    "name": features.name,
                    "path": features.path,
                    "size": features.size_bytes,
                    "q_values": dict(q_values),
                    "confidence": confidence,
                }
            )

        return recommendations

    def save_q_table(self, path: Path):
        """Save Q-table to file."""
        data = {
            "q_table": dict(self.q_table),
            "params": {
                "lr": self.lr,
                "gamma": self.gamma,
                "epsilon": self.epsilon,
                "epsilon_decay": self.epsilon_decay,
            },
            "saved_at": datetime.now().isoformat(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Q-Table saved to {path}")

    def load_q_table(self, path: Path):
        """Load Q-table from file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.q_table = defaultdict(lambda: defaultdict(float))
        for state, actions in data["q_table"].items():
            for action, q_value in actions.items():
                self.q_table[state][action] = q_value
        print(f"Q-Table loaded from {path}")


class DesktopScanner:
    """Scans desktop and extracts file features."""

    def __init__(self, desktop_path: Path):
        self.desktop_path = desktop_path
        self.ecosystem_markers = {
            "Azazael",
            "BIBL",
            "CODEX",
            "HORUS",
            "HYDRA",
            "KLINK",
            "METAWORMS",
            "PERSEPHONE",
            "POINDEXTER",
            "SHORTY",
            "TARBABY",
            "TERENCE",
            "TWITLER",
            "WORMZ",
        }

    def scan(self) -> list[FileFeatures]:
        """Scan desktop and extract features for all files."""
        files = []
        now = datetime.now()

        for item in self.desktop_path.iterdir():
            if item.name.startswith("."):
                continue

            modified = item.stat().st_mtime
            modified_days = (now - datetime.fromtimestamp(modified)).days

            access = item.stat().st_atime
            access_days = (now - datetime.fromtimestamp(access)).days

            features = FileFeatures(
                name=item.name,
                path=str(item),
                size_bytes=item.stat().st_size if item.is_file() else 0,
                modified_days_ago=modified_days,
                is_shortcut=item.suffix == ".lnk",
                is_text=item.suffix
                in {".md", ".txt", ".json", ".py", ".html", ".css", ".js"},
                is_code=item.suffix
                in {".py", ".js", ".ts", ".java", ".cpp", ".c", ".h"},
                is_doc=item.suffix in {".doc", ".docx", ".pdf", ".xls", ".xlsx"},
                ecosystem_related=any(
                    marker in item.name for marker in self.ecosystem_markers
                ),
                last_access_days=access_days,
                extension=item.suffix[1:] if item.suffix else "",
            )
            files.append(features)

        return files

    def get_ecosystem_info(self) -> dict[str, Any]:
        """Get information about ecosystem components."""
        info = {
            "scan_date": datetime.now().isoformat(),
            "components": {},
            "statistics": {},
        }

        for component in self.ecosystem_markers:
            path = self.desktop_path / component
            if path.exists():
                if path.is_dir():
                    files = list(path.rglob("*"))
                    info["components"][component] = {
                        "type": "directory",
                        "file_count": len([f for f in files if f.is_file()]),
                        "dir_count": len([f for f in files if f.is_dir()]),
                        "size_bytes": sum(
                            f.stat().st_size for f in files if f.is_file()
                        ),
                    }
                else:
                    info["components"][component] = {
                        "type": "file",
                        "size_bytes": path.stat().st_size,
                    }

        total_size = sum(
            c["size_bytes"] if isinstance(c.get("size_bytes"), (int, float)) else 0
            for c in info["components"].values()
        )
        total_files = sum(
            c.get("file_count", 0)
            if isinstance(c.get("file_count"), (int, float))
            else 0
            for c in info["components"].values()
        )

        info["statistics"] = {
            "total_components": len(info["components"]),
            "total_files": total_files,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
        }

        return info


def create_environment_info(
    scanner: DesktopScanner, optimizer: QLearningFileOptimizer
) -> dict[str, Any]:
    """Create comprehensive environment information with Q-Learning insights."""
    files = scanner.scan()
    ecosystem = scanner.get_ecosystem_info()

    recommendations = optimizer.evaluate(files)

    info = {
        "metadata": {
            "created": datetime.now().isoformat(),
            "version": "2.0.0",
            "generator": "QLearningFileOptimizer",
        },
        "ecosystem": ecosystem,
        "files_analyzed": len(files),
        "recommendations": {
            "keep_count": len(recommendations["KEEP"]),
            "delete_count": len(recommendations["DELETE"]),
            "review_count": len(recommendations["REVIEW"]),
            "details": recommendations,
        },
        "q_learning": {
            "table_size": len(optimizer.q_table),
            "epsilon": optimizer.epsilon,
            "actions": optimizer.actions,
        },
    }

    return info


def save_environment_report(info: dict, path: Path):
    """Save environment report to file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, default=str)
    print(f"Environment report saved to {path}")


def generate_markdown_report(info: dict, path: Path):
    """Generate markdown report from environment info."""
    md = f"""# Azazael Ecosystem Environment Report

**Generated:** {info["metadata"]["created"]}  
**Version:** {info["metadata"]["version"]}

## Ecosystem Statistics

| Metric | Value |
|--------|-------|
| Components | {info["ecosystem"]["statistics"]["total_components"]} |
| Total Files | {info["ecosystem"]["statistics"]["total_files"]} |
| Total Size | {info["ecosystem"]["statistics"]["total_size_mb"]} MB |

## Component Details

| Component | Type | Files | Size |
|-----------|------|-------|------|
"""

    for name, data in info["ecosystem"]["components"].items():
        file_count = data.get("file_count", "-")
        size = data.get("size_bytes", 0)
        size_mb = round(size / (1024 * 1024), 2) if size else "-"
        md += f"| {name} | {data['type']} | {file_count} | {size_mb} MB |\n"

    md += f"""
## Q-Learning Recommendations

### Summary
- **Keep:** {info["recommendations"]["keep_count"]} files
- **Delete:** {info["recommendations"]["delete_count"]} files  
- **Review:** {info["recommendations"]["review_count"]} files

### Delete Candidates
| File | Size | Confidence |
|------|------|------------|
"""

    for item in info["recommendations"]["details"]["DELETE"][:10]:
        size_kb = round(item["size"] / 1024, 1)
        md += f"| {item['name']} | {size_kb} KB | {item['confidence']:.2f} |\n"

    md += """
### Review Candidates
| File | Size | Confidence |
|------|------|------------|
"""

    for item in info["recommendations"]["details"]["REVIEW"][:10]:
        size_kb = round(item["size"] / 1024, 1)
        md += f"| {item['name']} | {size_kb} KB | {item['confidence']:.2f} |\n"

    md += f"""
## Q-Learning Model

- **Table Size:** {info["q_learning"]["table_size"]} states
- **Epsilon:** {info["q_learning"]["epsilon"]:.4f}
- **Actions:** {", ".join(info["q_learning"]["actions"])}

---
*Generated by QLearningFileOptimizer v{info["metadata"]["version"]}*
"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"Markdown report saved to {path}")


def main():
    """Main execution."""
    print("=" * 60)
    print("Azazael Ecosystem - QLEARNING FILE OPTIMIZER")
    print("Extended Q-Learning with Tables for Desktop Management")
    print("=" * 60)
    print()

    # Ensure output directory exists
    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)

    scanner = DesktopScanner(DESKTOP_PATH)
    optimizer = QLearningFileOptimizer(
    learning_rate=0.2,
    discount_factor=0.95,
    exploration_rate=1.0,
    exploration_decay=0.995,
    min_exploration=0.01,
    )

    print("[1/6] Scanning Desktop environment...")
    files = scanner.scan()
    print(f"      Found {len(files)} files")

    print("[2/6] Training Q-Learning agent...")
    optimizer.train(files, episodes=100)

    print("[3/6] Evaluating files...")
    recommendations = optimizer.evaluate(files)
    print(
    f"      KEEP: {len(recommendations['KEEP'])} | DELETE: {len(recommendations['DELETE'])} | REVIEW: {len(recommendations['REVIEW'])}"
    )

    print("[4/6] Collecting ecosystem information...")
    ecosystem_info = scanner.get_ecosystem_info()
    print(f"      Components: {ecosystem_info['statistics']['total_components']}")
    print(f"      Total Size: {ecosystem_info['statistics']['total_size_mb']} MB")

    print("[5/6] Generating environment information files...")

    info = create_environment_info(scanner, optimizer)

    json_path = OUTPUT_PATH / "ENVIRONMENT_INFO.json"
    save_environment_report(info, json_path)

    md_path = OUTPUT_PATH / "ENVIRONMENT_REPORT.md"
    generate_markdown_report(info, md_path)

    q_table_path = OUTPUT_PATH / "QTABLE.json"
    optimizer.save_q_table(q_table_path)

    print("[6/6] Finalizing...")

    print()
    print("=" * 60)
    print("RECOMMENDATIONS SUMMARY")
    print("=" * 60)
    print()
    print("DELETE (high confidence):")
    for item in recommendations["DELETE"][:5]:
        size_kb = round(item["size"] / 1024, 1)
        print(
        f"  - {item['name']} ({size_kb} KB) - Confidence: {item['confidence']:.2f}"
        )
        print()
        print("REVIEW (medium confidence):")
        for item in recommendations["REVIEW"][:5]:
            size_kb = round(item["size"] / 1024, 1)
            print(
            f"  - {item['name']} ({size_kb} KB) - Confidence: {item['confidence']:.2f}"
            )
            print()
            print("Files generated:")
            print(f"  - {json_path}")
            print(f"  - {md_path}")
            print(f"  - {q_table_path}")
            print()
            print("Done!")


if __name__ == "__main__":
    main()
