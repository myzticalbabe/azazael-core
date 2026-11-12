# coding: utf-8
"""
Auto-Discovering Configuration Manager with Live Lexicon Updates

Automatically discovers all modules in ml_platforms/, extracts documentation,
and keeps the BIBL lexicon current through recursive scanning.

Functions:
    - load_config()
    - list_platforms()
    - get_env_path()
    - list_workflows()
    - discover_modules()
    - update_lexicon()
    - watch_changes()

Classes:
    - MLPlatformConfig
    - LexiconUpdater

Usage:
    >>> import config
    >>> config.load_config()
    >>> config.update_lexicon()  # Auto-update BIBL
    >>> config.watch_changes(30)  # Live watch

Author: Auto-generated
Date: 2026-02-11
"""

import json
import re
import ast
import hashlib
import logging
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LexiconUpdater:
    """Auto-updates BIBL lexicon when modules change."""

    def __init__(self, base_path: (Path | None) = None):
        self.base_path = base_path or Path(__file__).parent.parent
        self.bibl_path = self.base_path / "BIBL"
        self.ml_path = self.base_path / "ml_platforms"
        self.module_hashes: dict[str, str] = {}

    def compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA256 hash of a file."""
        try:
            with open(file_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception as exc:
            note_failure("config:L60", exc)
            return ""

    def get_module_info(self, py_file: Path) -> dict[str, Any]:
        """Extract module documentation from Python file."""
        info: dict[str, Any] = {
            "name": py_file.stem,
            "path": str(py_file.relative_to(self.base_path)),
            "functions": [],
            "classes": [],
            "docstring": "",
            "categories": [],
        }

        try:
            with open(py_file, "r", encoding="utf-8") as f:
                content = f.read()

            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    class_info: dict[str, Any] = {
                        "name": node.name,
                        "docstring": ast.get_docstring(node) or "",
                    }
                    class_info["methods"] = [
                        n.name for n in node.body if isinstance(n, ast.FunctionDef)
                    ]
                    info["classes"].append(class_info)

                elif isinstance(node, ast.FunctionDef):
                    info["functions"].append(
                        {
                            "name": node.name,
                            "docstring": ast.get_docstring(node) or "",
                        }
                    )

            info["docstring"] = ast.get_docstring(tree) or ""

            cat_match = re.search(r"Category:\s*(\w+)", info["docstring"])
            if cat_match:
                info["categories"] = [cat_match.group(1)]

        except Exception as e:
            logger.error(f"Error parsing {py_file}: {e}")

        return info

    def scan_modules(self) -> dict[str, dict[str, Any]]:
        """Recursively scan ml_platforms for modules."""
        modules: dict[str, dict[str, Any]] = {}

        for py_file in self.ml_path.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue

            rel_path = str(py_file.relative_to(self.base_path))
            modules[rel_path] = self.get_module_info(py_file)
            self.module_hashes[rel_path] = self.compute_file_hash(py_file)

        return modules

    def generate_term_entry(self, module_info: dict, module_path: str) -> str:
        """Generate markdown entry for a term."""
        name = module_info["name"]
        docstring = module_info["docstring"]
        categories = module_info["categories"]
        cat_str = ", ".join(categories) if categories else "Core"

        lines = [
            f"### {name}",
            "",
            f"**Category:** {cat_str}",
            "",
            f"**File:** `{module_path}`",
            "",
            "**Description:**",
        ]

        if docstring:
            desc_match = re.search(
                r"(?:\*\*Definition:\*\*|Definition:)\s*(.+?)(?:\n\n|\*\*)",
                docstring,
                re.DOTALL,
            )
            if desc_match:
                lines.append(desc_match.group(1).strip())
            else:
                lines.append(docstring[:200].strip())

        if module_info["functions"]:
            lines.append("")
            lines.append("**Functions:**")
            for func in module_info["functions"][:5]:
                lines.append(f" - `{func['name']}()`")

        if module_info["classes"]:
            lines.append("")
            lines.append("**Classes:**")
            for cls in module_info["classes"][:5]:
                lines.append(f" - `{cls['name']}`")

        return "\n".join(lines)

    def update_bibl_index(self, modules: dict[str, dict[str, Any]]):
        """Update BIBL INDEX.md with discovered modules."""
        index_path = self.bibl_path / "INDEX.md"

        all_terms: list[dict[str, str]] = []
        for path, info in modules.items():
            name = info["name"]
            categories = info["categories"] or ["Core"]
            for cat in categories:
                all_terms.append({"term": name, "category": cat, "path": path})

        all_terms.sort(key=lambda x: x["term"].lower())

        cat_groups: dict[str, list[dict[str, str]]] = {}
        for term in all_terms:
            cat = term["category"]
            if cat not in cat_groups:
                cat_groups[cat] = []
            cat_groups[cat].append(term)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            "---",
            "title: Azazael Lexicon Index",
            "version: 1.1.0",
            f"last_updated: {datetime.now().strftime('%Y-%m-%d')}",
            "status: AUTO-GENERATED",
            "---",
            "",
            "# Azazael Lexicon",
            "",
            f"> Auto-generated from {len(modules)} modules",
            f"> Last updated: {timestamp}",
            "",
            "## Quick Navigation",
            "",
            "| Section | Terms |",
            "|---------|-------|",
            "| [A-F](A-F.md) | Terms starting with A-F |",
            "| [G-L](G-L.md) | Terms starting with G-L |",
            "| [M-R](M-R.md) | Terms starting with M-R |",
            "| [S-Z](S-Z.md) | Terms starting with S-Z |",
            "",
            f"**Total Modules:** {len(modules)}",
            "",
            "## Module Index",
            "",
        ]

        for path, info in sorted(modules.items(), key=lambda x: x[1]["name"].lower()):
            doc = info["docstring"] or "No description"
            lines.append(f"- [{info['name']}]({Path(path).stem}.md) - {doc[:60]}...")

        lines.extend(["", "## Categories", ""])

        for cat, terms in sorted(cat_groups.items()):
            lines.extend([f"### {cat}", "", "| Term | File |", "|------|------|"])
            for term in sorted(terms, key=lambda x: x["term"]):
                lines.append(
                    f"| [{term['term']}]({Path(term['path']).stem}.md) | `{term['path']}` |"
                )

        lines.extend(
            [
                "",
                "---",
                "",
                "*Auto-generated by config.py - Run `config.update_lexicon()` to refresh*",
            ]
        )

        index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info(f"Updated {index_path} with {len(modules)} modules")

    def update_all_bibl_files(self, modules: dict[str, dict[str, Any]]):
        """Regenerate all BIBL markdown files."""
        self.bibl_path.mkdir(parents=True, exist_ok=True)

        for path, info in modules.items():
            term_path = self.bibl_path / f"{info['name']}.md"
            content = self.generate_term_entry(info, path)
            cats = ", ".join(info["categories"]) if info["categories"] else "Core"
            with open(term_path, "w", encoding="utf-8") as f:
                f.write("---\n")
                f.write(f"title: {info['name']}\n")
                f.write("version: 1.0.0\n")
                f.write(f"last_updated: {datetime.now().strftime('%Y-%m-%d')}\n")
                f.write("status: AUTO-GENERATED\n")
                f.write(f"categories: {cats}\n")
                f.write("---\n\n")
                f.write(content)
                f.write("\n\n---\n\n*Auto-generated by config.py*\n")

        self.update_bibl_index(modules)
        logger.info(f"Generated {len(modules)} BIBL files")

    def check_for_changes(self) -> list[str]:
        """Check for modified modules."""
        changed: list[str] = []
        for py_file in self.ml_path.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            rel_path = str(py_file.relative_to(self.base_path))
            current_hash = self.compute_file_hash(py_file)
            old_hash = self.module_hashes.get(rel_path, "")
            if current_hash != old_hash:
                changed.append(rel_path)
                self.module_hashes[rel_path] = current_hash
        return changed

    def update_lexicon(self, full_regen: bool = False) -> dict[str, Any]:
        """Update BIBL lexicon to match current modules."""
        logger.info("Scanning ml_platforms/ for modules...")

        modules = self.scan_modules()

        if full_regen or len(modules) > len(self.module_hashes):
            self.update_all_bibl_files(modules)
        else:
            changed = self.check_for_changes()
            if changed:
                logger.info(f"Changed files: {changed}")
                for path in changed:
                    info = self.get_module_info(self.base_path / path)
                    term_path = self.bibl_path / f"{info['name']}.md"
                    with open(term_path, "w", encoding="utf-8") as f:
                        f.write("---\n")
                        f.write(f"title: {info['name']}\n")
                        f.write("version: 1.0.0\n")
                        f.write(
                            f"last_updated: {datetime.now().strftime('%Y-%m-%d')}\n"
                        )
                        f.write("status: AUTO-GENERATED\n")
                        f.write("---\n\n")
                        f.write(self.generate_term_entry(info, path))
                        f.write("\n\n---\n\n*Auto-generated by config.py*\n")
                self.update_bibl_index(modules)
                logger.info(f"Updated {len(changed)} changed files")
            else:
                logger.info("No changes detected")

        return {
            "modules_scanned": len(modules),
            "timestamp": datetime.now().isoformat(),
        }

    def watch(self, interval: int = 30):
        """Watch ml_platforms/ for changes and auto-update BIBL."""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            logger.warning("watchdog not installed. Using polling mode.")
            self._poll_watch(interval)
            return

        class FileChangeHandler(FileSystemEventHandler):
            def __init__(self, upd: "LexiconUpdater"):
                self.upd = upd
                self.last_trigger = 0

            def on_modified(self, event):
                if event.is_directory or not str(event.src_path).endswith(".py"):
                    return
                if time.time() - self.last_trigger < 2.0:
                    return
                self.last_trigger = time.time()
                logger.info(f"Detected change: {event.src_path}")
                self.upd.update_lexicon()

        observer = Observer()
        observer.schedule(FileChangeHandler(self), str(self.ml_path), recursive=True)
        observer.start()
        logger.info(f"Started live watch on {self.ml_path}")

        try:
            while True:
                time.sleep(interval)
        except KeyboardInterrupt as exc:
            note_failure("config:L347", exc)
            observer.stop()
        observer.join()

    def _poll_watch(self, interval: int):
        """Fallback polling when watchdog unavailable."""
        logger.info(f"Started polling watch (interval={interval}s)")
        try:
            while True:
                self.update_lexicon()
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Stopped polling watch")


class MLPlatformConfig:
    """Unified ML platform configuration manager."""

    DEFAULT_PATH = Path(__file__).parent.parent / "ml_platform_config.json"

    def __init__(self, path: (Path | None) = None):
        self.path = path or self.DEFAULT_PATH
        self._data: (dict | None) = None
        self._lexicon_updater = LexiconUpdater()
        self.load()

    def load(self) -> None:
        """Load configuration from JSON file."""
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._data = None

    @property
    def data(self) -> (dict | None):
        """Get loaded configuration data."""
        return self._data

    @property
    def platforms(self) -> list[dict[str, Any]]:
        """Get all platforms across all environments."""
        if not self._data:
            return []
        platforms: list[dict[str, Any]] = []
        for env in self._data.get("python_environments", {}).values():
            platforms.extend(env.get("packages", []))
        return platforms

    @property
    def total_platforms(self) -> int:
        """Get total count of platforms."""
        return len(self.platforms)

    def get_environment(self, name: str) -> (dict | None):
        """Get a specific environment by name."""
        if not self._data:
            return None
        return self._data.get("python_environments", {}).get(name)

    def list_by_category(self) -> dict[str, list[dict[str, Any]]]:
        """Group platforms by category."""
        categories: dict[str, list[dict[str, Any]]] = {}
        for pkg in self.platforms:
            cat = pkg.get("category", "other")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(pkg)
        return categories

    def get_workflows(self) -> list[dict[str, Any]]:
        """Get recommended workflows."""
        if not self._data:
            return []
        return self._data.get("learning_protocol", {}).get("recommended_workflows", [])

    def auto_discover(self) -> dict[str, Any]:
        """Auto-discover all modules and update BIBL."""
        return self._lexicon_updater.update_lexicon()


_config: (MLPlatformConfig | None) = None
_config_lock = threading.Lock()


def _get_config() -> MLPlatformConfig:
    """Get or create the global config instance — thread-safe."""
    global _config
    if _config is None:
        with _config_lock:
            if _config is None:
                _config = MLPlatformConfig()
    return _config


def load_config() -> (dict | None):
    """Load ML platform configuration."""
    return _get_config().data


def list_platforms(env: (str | None) = None) -> list[dict[str, Any]]:
    """list platforms, optionally filtered by environment."""
    config = _get_config()
    if not config.data:
        return []
    if env:
        env_data = config.get_environment(env)
        return env_data.get("packages", []) if env_data else []
    return config.platforms


def get_env_path(env_name: str) -> (str | None):
    """Get Python environment path."""
    config = _get_config()
    env_data = config.get_environment(env_name)
    return env_data.get("path") if env_data else None


def list_workflows() -> list[dict[str, Any]]:
    """list recommended workflows."""
    return _get_config().get_workflows()


def discover_modules() -> dict[str, dict[str, Any]]:
    """Auto-discover all modules in ml_platforms/."""
    updater = LexiconUpdater()
    return updater.scan_modules()


def update_lexicon(full_regen: bool = False) -> dict[str, Any]:
    """Update BIBL lexicon to match current modules."""
    updater = LexiconUpdater()
    return updater.update_lexicon(full_regen=full_regen)


def watch_changes(interval: int = 30):
    """Start live watching for changes and auto-updating BIBL."""
    updater = LexiconUpdater()
    updater.watch(interval)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "--watch":
            ival = int(sys.argv[2]) if len(sys.argv) > 2 else 30
            watch_changes(ival)
        elif sys.argv[1] == "--full":
            result = update_lexicon(full_regen=True)
            print(f"Generated {result['modules_scanned']} BIBL entries")
        elif sys.argv[1] == "--discover":
            modules = discover_modules()
            print(f"Discovered {len(modules)} modules")
    else:
        result = update_lexicon()
        print(f"Scanned {result['modules_scanned']} modules")
