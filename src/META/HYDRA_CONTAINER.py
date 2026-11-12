import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))

# coding: utf-8
"""
HYDRA_CONTAINER - Containerized Worm Execution with HYDRA Integration

Execute worms and clusters in isolated Docker containers with full
HYDRA ML cluster integration for optimized container deployment.

Features:
- Containerized worm execution
- HYDRA cluster integration
- Isolated dependency environments
- Resource limits and monitoring
- Parallel container orchestration
- Container lifecycle management

Usage:
    >>> from METAWORMS.HYDRA_CONTAINER import HYDRA_CONTAINER
    >>> hc = HYDRA_CONTAINER()
    >>> hc.run_worm_container("WORMZ")
    >>> hc.run_cluster_container("system")
    >>> hc.execute_hydra_trials(10)
"""

import subprocess
import json
import time
import hashlib
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, scrolledtext
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class ContainerExecution:
    """Container execution result."""

    execution_id: str
    container_id: str
    worm_name: str
    status: str
    duration_ms: float
    output: str
    exit_code: int
    started_at: str
    finished_at: str


@dataclass
class ClusterContainerConfig:
    """Configuration for cluster container."""

    cluster_name: str
    worms: list[str]
    base_image: str = "python:3.15-slim"
    memory_limit: str = "512m"
    cpu_limit: str = "0.5"
    network_mode: str = "none"
    volumes: dict[str, str] = field(default_factory=dict)


class HYDRA_CONTAINERConfig:
    """Configuration for HYDRA_CONTAINER."""

    OUTPUT_PATH = "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META/output/hydra_container"
    TRIALS_FILE = "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META/output/hydra_container/trials.json"
    STATE_FILE = "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META/output/hydra_container/state.json"

    WORKSPACE_PATH = "C:/Users/clint/Desktop"

    DEFAULT_IMAGE = "python:3.14t-slim"

    CONTAINER_PREFIX = "azazael_"

    RESOURCE_LIMITS = {
        "small": {"memory": "256m", "cpu": "0.25"},
        "medium": {"memory": "512m", "cpu": "0.5"},
        "large": {"memory": "1g", "cpu": "1.0"},
    }

    WORM_COMMANDS = {
        "SYSTEMZ": "python /app/WORMZ/SYSTEMZ.py --status",
        "PROCESSZ": "python /app/WORMZ/PROCESSZ.py --summary",
        "DISKZ": "python /app/WORMZ/DISKZ.py --usage",
        "NETWORXZ": "python /app/WORMZ/NETWORXZ.py --stats",
        "CLOCKZ": "python /app/WORMZ/CLOCKZ.py --time",
        "WORMZ": "python /app/WORMZ/WORMZ.py --all",
    }


class HYDRA_CONTAINER:
    """Containerized Worm Execution with HYDRA Integration."""

    VERSION = "1.0.0"

    def __init__(self):
        self.config = HYDRA_CONTAINERConfig()
        self.output_path = Path(self.config.OUTPUT_PATH)
        self._ensure_directories()

        self.executions: list[ContainerExecution] = []
        self.trials: list[dict] = []
        self.state: dict[str, Any] = {}

        self._docker_available = self._check_docker()
        self._load_state()

    def _ensure_directories(self):
        """Create output directories."""
        self.output_path.mkdir(parents=True, exist_ok=True)

    def _check_docker(self) -> bool:
        """Check if Docker is available."""
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=10,
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as exc:
            note_failure("HYDRA_CONTAINER:L131", exc)
            return False  # docker missing or unreachable

    def _run_docker(self, args: list[str], timeout: int = 300) -> tuple[bool, str, str]:
        """Run docker command."""
        cmd = ["docker"] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout,
            )
            return result.returncode == 0, result.stdout, result.stderr
        except subprocess.TimeoutExpired as exc:
            note_failure("HYDRA_CONTAINER:L145", exc)
            return False, "", "Command timed out"
        except FileNotFoundError as exc:
            note_failure("HYDRA_CONTAINER:L147", exc)
            return False, "", "Docker not found"
        except OSError as e:
            note_failure("HYDRA_CONTAINER:L149", e)
            return False, "", str(e)

    def _generate_dockerfile(self, worm_name: str) -> str:
        """Generate Dockerfile for worm execution."""
        dockerfile = f"""FROM {self.config.DEFAULT_IMAGE}

WORKDIR /app

COPY requirements-3.15t.txt .
RUN pip install --no-cache-dir -r requirements-3.15t.txt

COPY WORMZ/ ./WORMZ/
COPY METAWORMS/ ./METAWORMS/
COPY Azazael/ ./Azazael/

CMD ["python", "WORMZ/{worm_name}.py", "--status"]
"""
        return dockerfile

    def _create_container_config(self, worm_name: str) -> ClusterContainerConfig:
        """Create container configuration for worm."""
        return ClusterContainerConfig(
            cluster_name=f"{self.config.CONTAINER_PREFIX}{worm_name.lower()}",
            worms=[worm_name],
            volumes={
                self.config.WORKSPACE_PATH: "/app:ro",
            },
        )

    def run_worm_container(
        self,
        worm_name: str,
        timeout: int = 60,
        remove: bool = True,
    ) -> ContainerExecution:
        """Execute a single worm in a container."""
        execution_id = hashlib.md5(f"{time.time()}_{worm_name}".encode()).hexdigest()[
            :12
        ]

        started_at = datetime.now().isoformat()

        command = self.config.WORM_COMMANDS.get(worm_name)
        if not command:
            command = f"python /app/WORMZ/{worm_name}.py --status"

        args = [
            "run",
            "--rm",
            "--name",
            f"{self.config.CONTAINER_PREFIX}{execution_id}",
            "-v",
            f"{self.config.WORKSPACE_PATH}:/app:ro",
            "--network",
            "none",
            self.config.DEFAULT_IMAGE,
            "python",
            "-c",
            f"import sys; sys.path.insert(0, '/app'); {command.replace('python /app/', '')}",
        ]

        success, output, error = self._run_docker(args, timeout=timeout)
        duration_ms = 0

        finished_at = datetime.now().isoformat()
        if success and output:
            try:
                duration_ms = (
                    int(float(output.strip().split("\n")[-1].split()[-1]))
                    if "ms" in output
                    else 0
                )
            except (ValueError, IndexError) as exc:
                note_failure("HYDRA_CONTAINER:L222", exc)

        execution = ContainerExecution(
            execution_id=execution_id,
            container_id=f"{self.config.CONTAINER_PREFIX}{execution_id}",
            worm_name=worm_name,
            status="running" if success else "failed",
            duration_ms=duration_ms,
            output=output[:5000],
            exit_code=0 if success else 1,
            started_at=started_at,
            finished_at=finished_at,
        )

        self.executions.append(execution)
        return execution

    def run_cluster_container(
        self,
        cluster_type: str = "system",
        timeout: int = 120,
    ) -> list[ContainerExecution]:
        """Execute a cluster of worms in containers."""
        worms = {
            "system": ["SYSTEMZ", "PROCESSZ", "DISKZ"],
            "network": ["NETWORXZ", "CLOCKZ"],
            "full": ["SYSTEMZ", "PROCESSZ", "DISKZ", "NETWORXZ", "CLOCKZ"],
        }.get(cluster_type, ["SYSTEMZ"])

        executions = []
        for worm in worms:
            exec = self.run_worm_container(worm, timeout=timeout)
            executions.append(exec)

        return executions

    def run_parallel_cluster(
        self,
        cluster_type: str = "system",
        workers: int = 4,
    ) -> list[ContainerExecution]:
        """Execute cluster worms in parallel containers."""
        worms = {
            "system": ["SYSTEMZ", "PROCESSZ", "DISKZ"],
            "network": ["NETWORXZ", "CLOCKZ"],
            "full": ["SYSTEMZ", "PROCESSZ", "DISKZ", "NETWORXZ", "CLOCKZ"],
        }.get(cluster_type, ["SYSTEMZ"])

        executions = []
        with ThreadPoolExecutor(max_workers=min(workers, len(worms))) as executor:
            futures = {
                executor.submit(self.run_worm_container, worm): worm for worm in worms
            }
            for future in as_completed(futures):
                exec_result = future.result()
                executions.append(exec_result)

        return executions

    def run_hydra_trial(
        self,
        worm_sequence: (list[str] | None) = None,
        resource_profile: str = "medium",
    ) -> dict[str, Any]:
        """Execute HYDRA-style optimization trial in container."""
        trial_id = hashlib.md5(f"{time.time()}".encode()).hexdigest()[:8]
        worms = worm_sequence or ["SYSTEMZ", "PROCESSZ", "DISKZ"]

        limits = self.config.RESOURCE_LIMITS.get(
            resource_profile, self.config.RESOURCE_LIMITS["medium"]
        )

        start_time = time.time()
        executions = self.run_parallel_cluster("full", workers=len(worms))
        duration_ms = (time.time() - start_time) * 1000

        success_count = sum(1 for e in executions if e.status == "running")
        efficiency = (success_count / len(executions)) * 100 if executions else 0

        trial_result = {
            "trial_id": trial_id,
            "worm_sequence": worms,
            "duration_ms": round(duration_ms, 2),
            "success_count": success_count,
            "total_count": len(executions),
            "efficiency": round(efficiency, 2),
            "resource_profile": resource_profile,
            "executions": [
                {
                    "worm": e.worm_name,
                    "status": e.status,
                    "duration_ms": e.duration_ms,
                }
                for e in executions
            ],
            "timestamp": datetime.now().isoformat(),
        }

        self.trials.append(trial_result)
        self._save_trials()
        self._update_best(trial_result)

        return trial_result

    def run_hydra_trials(
        self,
        count: int = 10,
        parallel: bool = True,
    ) -> list[dict[str, Any]]:
        """Run multiple HYDRA optimization trials."""
        self.trials = []

        sequences = [
            ["SYSTEMZ", "PROCESSZ", "DISKZ"],
            ["DISKZ", "SYSTEMZ", "PROCESSZ"],
            ["PROCESSZ", "DISKZ", "SYSTEMZ"],
            ["SYSTEMZ", "DISKZ", "PROCESSZ"],
        ]

        results = []
        for i in range(count):
            sequence = sequences[i % len(sequences)]
            if parallel:
                result = self.run_hydra_trial(sequence)
            else:
                result = self.run_hydra_trial(sequence)
            results.append(result)

        return results

    def _update_best(self, trial: dict[str, Any]):
        """Update best trial in state."""
        current_best = self.state.get("best_efficiency", 0)
        if trial["efficiency"] > current_best:
            self.state["best_sequence"] = trial["worm_sequence"]
            self.state["best_efficiency"] = trial["efficiency"]
            self.state["best_trial_id"] = trial["trial_id"]
            self._save_state()

    def analyze_trials(self) -> dict[str, Any]:
        """Analyze trial results."""
        if not self.trials:
            return {"message": "No trials run yet"}

        efficiencies = [t["efficiency"] for t in self.trials]
        durations = [t["duration_ms"] for t in self.trials]

        return {
            "total_trials": len(self.trials),
            "avg_efficiency": sum(efficiencies) / max(len(efficiencies), 1),
            "best_efficiency": max(efficiencies) if efficiencies else 0,
            "avg_duration_ms": sum(durations) / max(len(durations), 1),
            "best_sequence": self.state.get("best_sequence", []),
            "best_trial_id": self.state.get("best_trial_id", ""),
        }

    def get_optimized_sequence(self) -> tuple[list[str], dict[str, Any]]:
        """Get optimized worm sequence."""
        best = self.state.get("best_sequence", ["SYSTEMZ", "PROCESSZ", "DISKZ"])
        return best, {"efficiency": self.state.get("best_efficiency", 0)}

    def build_worm_image(
        self,
        worm_name: str,
        tag: str = "latest",
    ) -> dict[str, Any]:
        """Build dedicated image for worm."""
        temp_dir = Path(tempfile.mkdtemp())
        dockerfile_path = temp_dir / "Dockerfile"

        dockerfile = self._generate_dockerfile(worm_name)
        with open(dockerfile_path, "w", encoding="utf-8") as f:
            f.write(dockerfile)

        shutil.copytree(
            self.config.WORKSPACE_PATH,
            temp_dir / "app",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", ".git", "node_modules"),
        )

        args = [
            "build",
            "-f",
            str(dockerfile_path),
            "-",
            f"azazael/{worm_name.lower()}:{tag}",
            str(temp_dir),
        ]

        success, output, error = self._run_docker(args, timeout=600)

        shutil.rmtree(temp_dir)

        return {
            "success": success,
            "image": f"azazael/{worm_name.lower()}:{tag}",
            "error": error,
        }

    def run_container_image(
        self,
        image: str,
        command: (str | None) = None,
        timeout: int = 60,
    ) -> ContainerExecution:
        """Run pre-built container image."""
        execution_id = hashlib.md5(f"{time.time()}_{image}".encode()).hexdigest()[:12]
        started_at = datetime.now().isoformat()

        args = ["run", "--rm", "--name", f"exec_{execution_id}"]
        if command:
            args.append(command)
        args.append(image)

        success, output, error = self._run_docker(args, timeout=timeout)
        finished_at = datetime.now().isoformat()

        return ContainerExecution(
            execution_id=execution_id,
            container_id=f"exec_{execution_id}",
            worm_name=image.split("/")[-1] if "/" in image else image,
            status="running" if success else "failed",
            duration_ms=0,
            output=output[:5000],
            exit_code=0 if success else 1,
            started_at=started_at,
            finished_at=finished_at,
        )

    def get_container_stats(self) -> list[dict]:
        """Get statistics for all azazael containers."""
        success, output, _ = self._run_docker(
            [
                "ps",
                "-a",
                "--filter",
                f"name={self.config.CONTAINER_PREFIX}",
                "--format",
                "{{.ID}}|{{.Names}}|{{.Status}}",
            ]
        )

        containers = []
        for line in output.strip().split("\n"):
            if line:
                parts = line.split("|")
                if len(parts) >= 3:
                    containers.append(
                        {
                            "id": parts[0],
                            "name": parts[1],
                            "status": parts[2],
                        }
                    )

        return containers

    def cleanup_containers(self):
        """Clean up all azazael containers."""
        containers = self.get_container_stats()
        for container in containers:
            self._run_docker(["rm", "-f", container["id"]])

    def _save_trials(self):
        """Persist trials."""
        trials_file = Path(self.config.TRIALS_FILE)
        trials_file.parent.mkdir(parents=True, exist_ok=True)
        with open(trials_file, "w", encoding="utf-8") as f:
            json.dump(self.trials, f, indent=2)

    def _save_state(self):
        """Persist state."""
        state_file = Path(self.config.STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def _load_state(self):
        """Load persisted state."""
        state_file = Path(self.config.STATE_FILE)
        if state_file.exists():
            try:
                with open(state_file, encoding="utf-8") as f:
                    self.state = json.load(f)
            except (OSError, ValueError) as exc:
                note_failure("HYDRA_CONTAINER:L508", exc)
                self.state = {}  # unreadable or corrupt state file

    def get_status(self) -> dict[str, Any]:
        """Get HYDRA_CONTAINER status."""
        containers = self.get_container_stats()
        return {
            "version": self.VERSION,
            "docker_available": self._docker_available,
            "active_containers": len(
                [c for c in containers if "running" in c["status"]]
            ),
            "total_containers": len(containers),
            "trials_run": len(self.trials),
            "best_efficiency": self.state.get("best_efficiency", 0),
            "best_sequence": self.state.get("best_sequence", []),
        }

    def run_popup(self):
        """Run with GUI popup."""
        root = tk.Tk()
        root.title("HYDRA_CONTAINER - Containerized Execution")
        root.geometry("800x600")

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        trials_tab = ttk.Frame(notebook)
        notebook.add(trials_tab, text="Trials")

        containers_tab = ttk.Frame(notebook)
        notebook.add(containers_tab, text="Containers")

        status_label = ttk.Label(trials_tab, text="Initializing...", font=("Arial", 12))
        status_label.pack(pady=10)

        log_text = scrolledtext.ScrolledText(trials_tab, width=90, height=25)
        log_text.pack(fill="both", expand=True, padx=10, pady=10)

        def run_trial():
            result = self.run_hydra_trial()
            log_text.insert("end", json.dumps(result, indent=2) + "\n")
            refresh()

        def run_trials():
            results = self.run_hydra_trials(5)
            log_text.insert("end", f"Completed {len(results)} trials\n")
            refresh()

        def refresh():
            status = self.get_status()
            status_label.config(
                text=f"Docker: {'Available' if status['docker_available'] else 'Not Found'} | "
                f"Trials: {status['trials_run']} | "
                f"Best Efficiency: {status['best_efficiency']}%"
            )

        ttk.Button(trials_tab, text="Run Single Trial", command=run_trial).pack(pady=5)
        ttk.Button(trials_tab, text="Run 5 Trials", command=run_trials).pack(pady=5)
        ttk.Button(
            trials_tab,
            text="Analyze",
            command=lambda: log_text.insert(
                "end", json.dumps(self.analyze_trials(), indent=2) + "\n"
            ),
        ).pack(pady=5)
        ttk.Button(trials_tab, text="Cleanup", command=self.cleanup_containers).pack(
            pady=5
        )

        root.mainloop()


def main():
    """CLI entry point."""

    parser = argparse.ArgumentParser(
        description="HYDRA_CONTAINER - Containerized Worm Execution"
    )
    parser.add_argument("--version", action="version", version="1.0.0")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--run", help="Run single worm in container")
    parser.add_argument(
        "--cluster", choices=["system", "network", "full"], help="Run cluster"
    )
    parser.add_argument("--parallel", action="store_true", help="Run parallel")
    parser.add_argument("--trial", action="store_true", help="Run single trial")
    parser.add_argument("--trials", type=int, default=5, help="Run multiple trials")
    parser.add_argument("--analyze", action="store_true", help="Analyze trials")
    parser.add_argument("--cleanup", action="store_true", help="Cleanup containers")
    parser.add_argument("--containers", action="store_true", help="list containers")
    parser.add_argument("--build", help="Build image for worm")
    parser.add_argument("--popup", action="store_true", help="Launch GUI")

    args = parser.parse_args()
    hc = HYDRA_CONTAINER()

    if args.status:
        print(json.dumps(hc.get_status(), indent=2))
    elif args.run:
        result = hc.run_worm_container(args.run)
        print(
            json.dumps(
                {
                    "status": result.status,
                    "duration_ms": result.duration_ms,
                },
                indent=2,
            )
        )
    elif args.cluster:
        results = hc.run_cluster_container(args.cluster)
        print(f"Cluster {args.cluster}: {len(results)} executions")
    elif args.parallel:
        results = hc.run_parallel_cluster(args.cluster or "system")
        print(f"Parallel cluster: {len(results)} executions")
    elif args.trial:
        result = hc.run_hydra_trial()
        print(json.dumps(result, indent=2))
    elif args.trials:
        results = hc.run_hydra_trials(args.trials)
        print(f"Completed {len(results)} trials")
    elif args.analyze:
        print(json.dumps(hc.analyze_trials(), indent=2))
    elif args.cleanup:
        hc.cleanup_containers()
        print("Containers cleaned up")
    elif args.containers:
        print(json.dumps(hc.get_container_stats(), indent=2))
    elif args.build:
        result = hc.build_worm_image(args.build)
        print(f"Build: {result['success']}, Image: {result.get('image', 'N/A')}")
    elif args.popup:
        hc.run_popup()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()


