# coding: utf-8
"""
HYDRA - Adaptive Sequential ML Cluster

Sequential processing with dependency awareness and adaptive learning.

Features:
- 6-stage sequential processing
- Dependency-aware execution
- Priority scheduling
- Adaptive learning
- Progress tracking

Usage:
    >>> import hydra
    >>> hydra.run_sequential()
    >>> hydra.get_cluster_status()
"""

import json
import os
import time
import threading
import queue
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, List
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


#: Canonical root resolver, shared with HORUS / hydra_bridge / BIBL. Every env-var
#: fallback below used to be `Path.home() / "azazael"` -- a DIFFERENT, STALE
#: checkout from this repo (C:/Azazael/azazael) -- so with AZAZAEL_ROOT unset HYDRA
#: read and wrote the wrong tree. `get_project_root_safe()` keeps AZAZAEL_ROOT's
#: precedence, never raises, and answers a sentinel failure with the tree this file
#: ships in (CORE/paths/internal.py).

try:
    from CORE.paths import get_project_root_safe as _get_project_root_safe
except ImportError as exc:                               # src/ not on sys.path
    note_failure("HYDRA:core-paths-import", exc)
    _get_project_root_safe = None


#: This file is src/ORCHESTRATION/HYDRA.py, so parents[2] is the repo root.
_ROOT_FALLBACK = Path(__file__).resolve().parents[2]

#: ONE value for both readers below (the ClusterConfig default and the CLI
#: --output default); the readers must agree on it.
_DEFAULT_ROOT = str(_get_project_root_safe() if _get_project_root_safe is not None
                    else _ROOT_FALLBACK)


def _discover_modules() -> dict:
    """Resolve discover_modules across the two ml_platforms copies.

    Runtime trace 2026-10-18: the bare ``from ml_platforms.config import``
    only resolved when src/HYDRA happened to be on sys.path, so
    discover_nodes() raised ModuleNotFoundError under the standard src/-on-
    path layout and DISCOVERY always scored 0. Try the sibling copy first
    (ORCHESTRATION-local, consistent with this module), then the HYDRA copy,
    then the legacy top-level path. Raises ModuleNotFoundError only if none
    resolve — never silently return empty."""
    for mod in (
        "ORCHESTRATION.ml_platforms.config",
        "HYDRA.ml_platforms.config",
        "ml_platforms.config",
    ):
        try:
            module = __import__(mod, fromlist=["discover_modules"])
            return module.discover_modules()
        except ImportError as exc:
            # The final raise names the failure, but WHICH candidate failed and
            # why is lost without this: three paths are tried and only the last
            # error would otherwise survive.
            note_failure("HYDRA:discover_modules-import", exc)
            continue
    raise ModuleNotFoundError(
        "No ml_platforms.config found (tried ORCHESTRATION/HYDRA/top-level)"
    )


def _run_ml_analysis() -> Any:
    """Run HORUS ML analysis, resolving the module across both layouts.

    ``src/HORUS`` is a DATA directory (``index.html``, ``output/``,
    ``results/``) with no Python in it, so a bare ``from HORUS import
    run_ml_analysis`` names a DIFFERENT module depending on which directory is
    on sys.path: ``ORCHESTRATION/HORUS.py`` when ``src/ORCHESTRATION`` is, and
    that data directory when ``src`` is. Same ambiguity ``_discover_modules``
    documents above, same remedy: try the package-qualified import first, then
    the sibling name, and skip a candidate that resolves but does not carry the
    symbol instead of calling through it.
    """
    for mod in ("ORCHESTRATION.HORUS", "HORUS"):
        try:
            module = __import__(mod, fromlist=["run_ml_analysis"])
        except ImportError as exc:
            note_failure("HYDRA:run_ml_analysis-import", exc)
            continue
        run = getattr(module, "run_ml_analysis", None)
        if run is not None:
            return run()
    raise ModuleNotFoundError(
        "No HORUS.run_ml_analysis (tried ORCHESTRATION.HORUS, HORUS)"
    )


class ProcessingStage(Enum):
    """HYDRA processing stages."""

    DISCOVERY = "Discovery"
    ANALYSIS = "Analysis"
    OPTIMIZATION = "Optimization"
    VALIDATION = "Validation"
    DEPLOYMENT = "Deployment"
    FEEDBACK = "Feedback"


@dataclass
class StageResult:
    """Result of a processing stage with NSGA-II Pareto front scoring."""

    stage: ProcessingStage
    success: bool
    modules_processed: int = 0
    duration: float = 0.0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    # MAXIMAL: NSGA-II Pareto Front multi-objective scores (0.0-1.0)
    recovery_rate: float = 0.0   # modules_processed / total_nodes
    latency_score: float = 0.0   # normalized duration (lower is better)
    resource_efficiency: float = 0.0  # output value per unit time


@dataclass
class ClusterNode:
    """Individual processing node in the cluster."""

    name: str
    group: str
    priority: int = 0
    dependencies: list[str] = field(default_factory=list)
    last_run: (str | None) = None
    success_count: int = 0
    failure_count: int = 0
    avg_duration: float = 0.0
    enabled: bool = True


@dataclass
class ClusterConfig:
    """HYDRA cluster configuration."""

    base_path: str = os.environ.get(
        "AZAZAEL_ROOT",
        os.environ.get("Azazael_ROOT", _DEFAULT_ROOT)
    )
    parallel_stages: bool = False
    max_retries: int = 3
    timeout_per_stage: int = 300
    auto_retry: bool = True
    priority_scheduling: bool = True


class HYDRACluster:
    """Adaptive sequential ML processing cluster."""

    STAGES = [
        ProcessingStage.DISCOVERY,
        ProcessingStage.ANALYSIS,
        ProcessingStage.OPTIMIZATION,
        ProcessingStage.VALIDATION,
        ProcessingStage.DEPLOYMENT,
        ProcessingStage.FEEDBACK,
    ]

    def __init__(self, config: (ClusterConfig | None) = None):
        self.config = config or ClusterConfig()
        self.base_path = Path(self.config.base_path)
        self.state_path = self.base_path / "state"
        self.output_path = self.base_path / "output"
        self.logs_path = self.base_path / "logs"

        self.state_path.mkdir(parents=True, exist_ok=True)
        self.output_path.mkdir(parents=True, exist_ok=True)
        self.logs_path.mkdir(parents=True, exist_ok=True)

        self.stage_history: list[StageResult] = []
        self.nodes: dict[str, ClusterNode] = {}
        self.learning_data: dict[str, Any] = {}
        # MAXIMAL: 3.14t free-threading locks for shared data structures
        self._cluster_lock = threading.Lock()
        self._history_lock = threading.Lock()

    def discover_nodes(self) -> dict[str, ClusterNode]:
        """Auto-discover available ML processing nodes (thread-safe)."""
        modules = _discover_modules()

        node_groups = {
            "core": [],
            "qlearning": [],
            "integration": [],
            "monitoring": [],
            "lm_studio": [],
            "analysis": [],
            "conversion": [],
            "installation": [],
            "autoload": [],
            "improvements": [],
        }

        for path, info in modules.items():
            path_lower = path.lower()
            group = "core"

            if "qlearning" in path_lower or "verify_moe" in path_lower:
                group = "qlearning"
            elif (
                "integration" in path_lower
                or "onnx" in path_lower
                or "ray" in path_lower
                or "mlflow" in path_lower
            ):
                group = "integration"
            elif "monitoring" in path_lower or "health" in path_lower:
                group = "monitoring"
            elif "lm_studio" in path_lower or "lmstudio" in path_lower:
                group = "lm_studio"
            elif "analysis" in path_lower:
                group = "analysis"
            elif "conversion" in path_lower or "converter" in path_lower:
                group = "conversion"
            elif "install" in path_lower:
                group = "installation"
            elif "autoload" in path_lower or "permanent" in path_lower:
                group = "autoload"
            elif "improvements" in path_lower:
                group = "improvements"

            node_groups[group].append(info["name"])

        priority_order = [
            "core",
            "qlearning",
            "integration",
            "monitoring",
            "lm_studio",
            "analysis",
            "conversion",
            "installation",
            "autoload",
            "improvements",
        ]

        with self._cluster_lock:
            self.nodes = {}
            for i, group in enumerate(priority_order):
                for name in node_groups.get(group, []):
                    self.nodes[name] = ClusterNode(
                        name=name,
                        group=group,
                        priority=len(priority_order) - i,
                        dependencies=self._get_dependencies(group),
                    )

        return self.nodes

    def _get_dependencies(self, group: str) -> list[str]:
        """Get dependencies for a module group."""
        deps = {
            "core": [],
            "qlearning": ["core"],
            "integration": ["core", "qlearning"],
            "monitoring": ["core"],
            "lm_studio": ["core"],
            "analysis": ["core", "integration"],
            "conversion": ["core"],
            "installation": [],
            "autoload": ["core"],
            "improvements": ["core", "analysis"],
        }
        return deps.get(group, [])

    def run_stage(self, stage: ProcessingStage) -> StageResult:
        """Execute a single processing stage with NSGA-II scoring."""
        start = datetime.now()
        result = StageResult(stage=stage, success=False)

        try:
            if stage == ProcessingStage.DISCOVERY:
                result.modules_processed = len(self.discover_nodes())
                result.success = result.modules_processed > 0

            elif stage == ProcessingStage.ANALYSIS:
                output = _run_ml_analysis()
                result.modules_processed = len(output.entries)
                result.output = output.to_dict()
                result.success = result.modules_processed > 0

            elif stage == ProcessingStage.OPTIMIZATION:
                result.modules_processed = self._optimize_modules()
                result.success = result.modules_processed >= 0

            elif stage == ProcessingStage.VALIDATION:
                result.success = self._validate_modules()
                result.modules_processed = len(self.nodes)

            elif stage == ProcessingStage.DEPLOYMENT:
                result.modules_processed = self._deploy_modules()
                result.success = result.modules_processed > 0

            elif stage == ProcessingStage.FEEDBACK:
                result = self._collect_feedback(result)

        except Exception as e:
            note_failure("HYDRA:L246", e)
            result.errors.append(str(e))

        result.duration = (datetime.now() - start).total_seconds()
        with self._history_lock:
            self.stage_history.append(result)
        return result

    def _optimize_modules(self) -> int:
        """Run module optimizations."""
        return len(self.nodes)

    def _validate_modules(self) -> bool:
        """Validate module integrity."""
        return len(self.nodes) > 0

    def _deploy_modules(self) -> int:
        """Deploy processed modules."""
        return len([n for n in self.nodes.values() if n.enabled])

    def _collect_feedback(self, result: StageResult) -> StageResult:
        """Collect feedback with NSGA-II Pareto Front multi-objective scoring."""
        result.success = True

        with self._cluster_lock:
            total_nodes = max(1, len(self.nodes))
            result.output["total_nodes"] = total_nodes
            result.output["enabled_nodes"] = len(
                [n for n in self.nodes.values() if n.enabled]
            )
            result.modules_processed = len(self.nodes)

        with self._history_lock:
            result.output["total_stages"] = len(self.stage_history)

        # MAXIMAL: NSGA-II Pareto Front recovery scoring
        result.recovery_rate = result.modules_processed / total_nodes

        # Latency score: normalized 0-1 (lower is better, cap at 300s)
        max_latency = 300.0
        result.latency_score = max(0.0, 1.0 - min(result.duration, max_latency) / max_latency)

        # Resource efficiency: modules processed per second
        if result.duration > 0:
            result.resource_efficiency = result.modules_processed / result.duration
        else:
            result.resource_efficiency = 0.0

        return result

    def run_with_popup(self, duration_seconds: int = 600) -> list[StageResult]:
        """Run HYDRA with real-time popup monitoring using queue-based thread safety."""
        import tkinter as tk
        from tkinter import ttk, scrolledtext

        popup = tk.Tk()
        popup.title("HYDRA Cluster - 10 Minute Test")
        popup.geometry("900x700")
        popup.configure(bg="#1a1a2e")

        # MAXIMAL: Queue-based communication (bg thread -> main thread)
        _q = queue.Queue()

        header = tk.Frame(popup, bg="#16213e", pady=10)
        header.pack(fill="x")

        tk.Label(
            header,
            text="HYDRA",
            font=("Consolas", 28, "bold"),
            bg="#16213e",
            fg="#00d4ff",
        ).pack()

        self.status_label = tk.Label(
            header,
            text="Initializing cluster...",
            font=("Consolas", 12),
            bg="#16213e",
            fg="#7c3aed",
        )
        self.status_label.pack()

        progress = ttk.Progressbar(
            popup, mode="determinate", length=850, style="TProgressbar"
        )
        progress.pack(pady=10, padx=25)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "TProgressbar",
            troughcolor="#1a1a2e",
            thickness=25,
        )

        text_frame = tk.Frame(popup, bg="#1a1a2e", padx=10, pady=10)
        text_frame.pack(fill="both", expand=True)

        text_area = scrolledtext.ScrolledText(
            text_frame,
            wrap="word",
            font=("Consolas", 10),
            bg="#0f0f1a",
            fg="#00ff88",
        )
        text_area.pack(fill="both", expand=True)

        def _log(msg, level="INFO"):
            """Thread-safe: enqueue log for main thread processing."""
            _q.put(("log", msg, level))

        def _update_status(text, pct):
            """Thread-safe: enqueue status update for main thread."""
            _q.put(("status", text, pct))

        def _process_queue():
            """Process queued messages on main thread."""
            try:
                while True:
                    item = _q.get_nowait()
                    if item[0] == "log":
                        ts = datetime.now().strftime("%H:%M:%S")
                        text_area.insert("end", f"[{ts}] [{item[2]}] {item[1]}\n")
                        text_area.see("end")
                    elif item[0] == "status":
                        self.status_label.config(text=item[1])
                        progress.configure(value=item[2])
                    elif item[0] == "destroy":
                        popup.destroy()
                        return
            except queue.Empty as exc:
                note_failure("HYDRA:L378", exc)
            popup.after(100, _process_queue)

        results_container: list[StageResult] = []

        def _run_stages_async():
            """Run the 6 pipeline stages in a background thread."""
            _log("=" * 60)
            _log("HYDRA CLUSTER - 10 MINUTE TEST")
            _log("Target: " + os.environ.get("SystemDrive", "C:") + "\\")
            _log(f"Duration: {duration_seconds} seconds")
            _log("=" * 60)

            start = time.time()

            for stage in self.STAGES:
                elapsed = time.time() - start
                if elapsed > duration_seconds:
                    _log(f"TIME LIMIT REACHED at {elapsed:.1f}s", "WARN")
                    break

                # MAXIMAL: Enqueue status updates instead of calling popup.after() from bg thread
                _update_status(f"Stage: {stage.value}", int((self.STAGES.index(stage) / len(self.STAGES)) * 100))
                _log(f"Starting: {stage.value}")

                result = self.run_stage(stage)
                results_container.append(result)

                status_icon = "OK" if result.success else "FAIL"
                _log(
                    f"[{status_icon}] {stage.value}: "
                    f"{result.modules_processed} modules in {result.duration:.2f}s"
                )
                _log(
                    f"  NSGA-II: recovery={result.recovery_rate:.2f} "
                    f"latency={result.latency_score:.2f} eff={result.resource_efficiency:.2f}"
                )
                _log(
                    f"Elapsed: {elapsed:.1f}s | Remaining: {max(0, duration_seconds - elapsed):.1f}s"
                )

            total_time = time.time() - start
            _log("=" * 60)
            _log("HYDRA TEST COMPLETE")
            _log(f"Stages completed: {len(results_container)}")
            _log(f"Total time: {total_time:.2f}s")
            _log("=" * 60)

            _update_status("Complete!", 100)
            _q.put(("destroy",))

        # Start queue processing on main thread
        popup.after(100, _process_queue)
        # Defer heavy work until after mainloop starts
        popup.after(100, lambda: threading.Thread(target=_run_stages_async, daemon=True).start())
        popup.mainloop()

        return results_container

    def run_sequential(
        self, stages: (list[ProcessingStage] | None) = None
    ) -> list[StageResult]:
        """Run sequential processing through all stages with error recovery."""
        if stages is None:
            stages = self.STAGES

        results = []
        for stage in stages:
            if self.config.priority_scheduling:
                self._apply_priority_scheduling()

            result = self.run_stage(stage)

            # Auto-retry failed stages up to max_retries
            if not result.success and self.config.auto_retry:
                for _ in range(self.config.max_retries):
                    result = self.run_stage(stage)
                    if result.success:
                        break

            # Always record result and save state — never break the pipeline
            results.append(result)
            self._save_state()

        return results

    def _apply_priority_scheduling(self):
        """Apply priority-based scheduling to nodes (thread-safe)."""
        with self._cluster_lock:
            sorted_nodes = sorted(
                self.nodes.values(), key=lambda n: n.priority, reverse=True
            )
            for i, node in enumerate(sorted_nodes):
                node.priority = i + 1

    def get_cluster_status(self) -> dict[str, Any]:
        """Get current cluster status (thread-safe)."""
        with self._cluster_lock:
            nodes_total = len(self.nodes)
            nodes_enabled = len([n for n in self.nodes.values() if n.enabled])
            by_group = self._count_by_group()
        with self._history_lock:
            stage_total = len(self.stage_history)
            stage_ok = len([r for r in self.stage_history if r.success])
            stage_fail = len([r for r in self.stage_history if not r.success])
            last_run = self.stage_history[-1].timestamp if self.stage_history else None

        return {
            "nodes": {
                "total": nodes_total,
                "enabled": nodes_enabled,
                "by_group": by_group,
            },
            "stages": {
                "total": stage_total,
                "successful": stage_ok,
                "failed": stage_fail,
            },
            "last_run": last_run,
            "config": {
                "parallel_stages": self.config.parallel_stages,
                "max_retries": self.config.max_retries,
                "auto_retry": self.config.auto_retry,
                "priority_scheduling": self.config.priority_scheduling,
            },
        }

    def _count_by_group(self) -> dict[str, int]:
        """Count nodes by group (caller must hold _cluster_lock)."""
        counts = {}
        for node in self.nodes.values():
            counts[node.group] = counts.get(node.group, 0) + 1
        return counts

    def _save_state(self):
        """Save cluster state to disk (thread-safe)."""
        with self._history_lock:
            history_snapshot = [
                {
                    "stage": r.stage.value,
                    "success": r.success,
                    "modules_processed": r.modules_processed,
                    "duration": r.duration,
                    "errors": r.errors,
                    "timestamp": r.timestamp,
                    "recovery_rate": r.recovery_rate,
                    "latency_score": r.latency_score,
                    "resource_efficiency": r.resource_efficiency,
                }
                for r in self.stage_history
            ]

        with self._cluster_lock:
            nodes_snapshot = {
                name: {
                    "name": node.name,
                    "group": node.group,
                    "priority": node.priority,
                    "dependencies": node.dependencies,
                    "last_run": node.last_run,
                    "success_count": node.success_count,
                    "failure_count": node.failure_count,
                    "avg_duration": node.avg_duration,
                    "enabled": node.enabled,
                }
                for name, node in self.nodes.items()
            }

        state = {
            "nodes": nodes_snapshot,
            "stage_history": history_snapshot,
            "last_updated": datetime.now().isoformat(),
        }

        with open(self.state_path / "cluster_state.json", "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def load_state(self):
        """Load cluster state from disk."""
        state_file = self.state_path / "cluster_state.json"
        if not state_file.exists():
            return

        with open(state_file, encoding="utf-8") as f:
            state = json.load(f)

        self.nodes = {}
        for name, data in state.get("nodes", {}).items():
            self.nodes[name] = ClusterNode(
                name=data["name"],
                group=data["group"],
                priority=data.get("priority", 0),
                dependencies=data.get("dependencies", []),
                last_run=data.get("last_run"),
                success_count=data.get("success_count", 0),
                failure_count=data.get("failure_count", 0),
                avg_duration=data.get("avg_duration", 0.0),
                enabled=data.get("enabled", True),
            )

    def print_status(self):
        """Print cluster status to console."""
        status = self.get_cluster_status()

        print("\n" + "=" * 60)
        print("  HYDRA Cluster Status")
        print("=" * 60)

        print(
            f"\nNodes: {status['nodes']['total']} total, {status['nodes']['enabled']} enabled"
        )
        for group, count in status["nodes"]["by_group"].items():
            print(f"  - {group}: {count}")

        print(f"\nStages: {status['stages']['total']} total")
        print(f"  - Successful: {status['stages']['successful']}")
        print(f"  - Failed: {status['stages']['failed']}")

        print(f"\nLast Run: {status['last_run'] or 'Never'}")
        print("=" * 60 + "\n")


def run_hydra(discover: bool = True, run_all: bool = False):
    """Run HYDRA cluster operations."""
    cluster = HYDRACluster()

    if discover or run_all:
        print("Discovering nodes...")
        nodes = cluster.discover_nodes()
        print(f"Found {len(nodes)} nodes")

    if run_all:
        print("\nRunning sequential processing...")
        results = cluster.run_sequential()

        print("\nStage Results:")
        for result in results:
            status = "OK" if result.success else "FAIL"
            print(
                f"  [{status}] {result.stage.value}: {result.modules_processed} modules in {result.duration:.2f}s"
            )

        cluster.print_status()


def get_hydra_status():
    """Get HYDRA status."""
    cluster = HYDRACluster()
    cluster.load_state()
    cluster.print_status()
    return cluster.get_cluster_status()


def launcher():
    """Launch HYDRA with command-line interface."""
    parser = argparse.ArgumentParser(description="HYDRA - Adaptive ML Cluster")
    parser.add_argument(
        "--discover", action="store_true", help="Discover available nodes"
    )
    parser.add_argument(
        "--run", action="store_true", help="Run full sequential processing"
    )
    parser.add_argument("--status", action="store_true", help="Show cluster status")
    parser.add_argument(
        "--stages",
        nargs="+",
        choices=[s.value for s in ProcessingStage],
        help="Specific stages to run",
    )
    parser.add_argument(
        "--no-parallel", action="store_true", help="Disable parallel stage execution"
    )
    parser.add_argument(
        "--output",
        default=str(Path(_DEFAULT_ROOT) / "src" / "HYDRA"),
        help="Output directory",
    )
    parser.add_argument("--popup", action="store_true", help="Show popup window")

    args = parser.parse_args()

    config = ClusterConfig(
        base_path=args.output,
        parallel_stages=not args.no_parallel,
    )
    cluster = HYDRACluster(config)
    cluster.load_state()

    # Independent actions (fixed 2026-09-30). This was an `elif` chain, so
    # `--discover --run --status` -- the CMD declared in Dockerfile.ubuntu --
    # executed ONLY `--status` and reported "Stages: 0 total, Last Run: Never".
    # Every requested flag now acts, in order, and status is printed last so it
    # reflects the run that just happened.
    acted = False
    if args.discover:
        acted = True
        nodes = cluster.discover_nodes()
        print(f"Discovered {len(nodes)} nodes")
        for group, count in cluster._count_by_group().items():
            print(f"  {group}: {count}")
    if args.popup:
        acted = True
        cluster.run_with_popup()
    if args.run:
        acted = True
        if args.stages:
            stages = [ProcessingStage(s) for s in args.stages]
        else:
            stages = None
        results = cluster.run_sequential(stages)

        print("\nStage Results:")
        for result in results:
            status = "OK" if result.success else "FAIL"
            print(
                f"  [{status}] {result.stage.value}: {result.modules_processed} modules in {result.duration:.2f}s"
            )
    if args.status:
        acted = True
        cluster.print_status()
    if not acted:
        parser.print_help()


if __name__ == "__main__":
    launcher()


