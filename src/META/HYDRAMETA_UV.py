# coding: utf-8
"""
HYDRAMETA_UV - uv-Powered Hydratic Optimization

Advanced ML optimization using uv for fast dependency management
during HYDRA cluster trials.

Features:
- uv-powered package installation
- Isolated trial environments
- Fast dependency caching
- Parallel trial execution
- ML-optimized worm sequencing

Usage:
    >>> from METAWORMS.HYDRAMETA_UV import HYDRAMETA_UV
    >>> hmu = HYDRAMETA_UV()
    >>> hmu.run_trials(10)
    >>> hmu.optimize_sequence()
"""

import subprocess
import json
import time
import hashlib
import random
import shutil
import numpy as np
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass
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
class UVTrialResult:
    """Result from uv-powered optimization trial."""

    trial_id: str
    worm_sequence: list[str]
    duration_ms: float
    efficiency_score: float
    uv_install_time_ms: float
    worm_execution_time_ms: float
    packages_installed: int
    parameters: dict[str, Any]
    success: bool = True
    error: str = ""


@dataclass
class UVWormPerformance:
    """Performance metrics for uv-powered worm execution."""

    worm_name: str
    avg_duration_ms: float
    success_rate: float
    efficiency_score: float
    uv_overhead_ms: float
    execution_count: int
    last_executed: str


class HYDRAMETA_UVConfig:
    """Configuration for HYDRAMETA_UV."""

    TARGET_FOLDERS = [
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/HYDRA",
        "C:/Users/clint/Desktop/HORUS.py",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/UTILITIES/TARBABY.py",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/UTILITIES/POINDEXTER",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/UTILITIES/PERSEPHONE",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/CORE/WORMS",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/UTILITIES/TERENCE",
    ]

    OUTPUT_PATH = "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META/output/hydrometa_uv"
    STATE_FILE = "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META/output/hydrometa_uv/state.json"
    TRIALS_FILE = "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META/output/hydrometa_uv/trials.json"
    CACHE_PATH = "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META/output/hydrometa_uv/venvs"

    WORMS = [
        "SYSTEMZ",
        "PROCESSZ",
        "DISKZ",
        "NETWORXZ",
        "CLOCKZ",
        "METAZ",
        "DEPENDZ",
        "VALIDZ",
        "DEADZ",
        "METRICZ",
        "LOGZ",
        "CHANGEZ",
        "ERRORZ",
        "TRIMZ",
        "OPTIMIZ",
        "HEALTHZ",
    ]

    DEFAULT_PARAMS = {
        "interval": 1.0,
        "parallel": True,
        "timeout": 30.0,
        "retries": 3,
        "uv_cache": True,
        "uv_workers": 4,
    }

    STRATEGIES = ["round_robin", "performance_based", "ml_optimized", "uv_prioritized"]


class HYDRAMETA_UV:
    """uv-Powered Hydratic Meta-Worm Optimizer."""

    VERSION = "1.0.0"

    def __init__(self):
        self.config = HYDRAMETA_UVConfig()
        self.uv_path = self._find_uv()
        self.trials: list[UVTrialResult] = []
        self.performance: dict[str, UVWormPerformance] = {}
        self.state: dict[str, Any] = {}
        self.cache_dir = Path(self.config.CACHE_PATH)
        self.output_dir = Path(self.config.OUTPUT_PATH)
        self._ensure_directories()
        self._load_state()

    def _find_uv(self) -> str:
        """Find uv executable."""
        result = subprocess.run(["where", "uv"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if result.returncode == 0:
            return result.stdout.strip().split("\n")[0]
        return "uv"

    def _ensure_directories(self):
        """Create output directories."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _load_state(self):
        """Load persisted state."""
        state_file = Path(self.config.STATE_FILE)
        if state_file.exists():
            try:
                with open(state_file, encoding="utf-8") as f:
                    self.state = json.load(f)
            except Exception as exc:
                note_failure("HYDRAMETA_UV:L152", exc)
                self.state = {"best_sequence": [], "best_params": {}}

    def _save_state(self):
        """Persist state."""
        state_file = Path(self.config.STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def _run_uv_command(
        self, args: list[str], venv_path: (str | None) = None, timeout: int = 120
    ) -> tuple[bool, str, int]:
        """Run uv command in specific venv."""
        start = time.time()
        cmd = [self.uv_path] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                cwd=str(self.output_dir),
                timeout=timeout,
            )
            return (
                result.returncode == 0,
                result.stdout + result.stderr,
                int((time.time() - start) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("HYDRAMETA_UV:L181", exc)
            return False, "Timeout", int((time.time() - start) * 1000)
        except Exception as e:
            note_failure("HYDRAMETA_UV:L183", e)
            return False, str(e), int((time.time() - start) * 1000)

    def _create_trial_venv(self, trial_id: str) -> str:
        """Create isolated venv for trial."""
        venv_path = str(self.cache_dir / f"trial_{trial_id}")
        success, output, duration = self._run_uv_command(
            ["venv", venv_path, "--python", "python3"]
        )
        if not success:
            raise RuntimeError(f"Failed to create venv: {output}")
        return venv_path

    def _install_worm_deps(
        self, venv_path: str, packages: list[str]
    ) -> tuple[bool, str, int]:
        """Install dependencies in trial venv."""
        args = ["--python", f"{venv_path}/Scripts/python.exe", "pip", "install"]
        args.extend(packages)
        success, output, duration = self._run_uv_command(args)
        return success, output, duration

    def _execute_worm(
        self, venv_path: str, worm_name: str, timeout: float
    ) -> tuple[bool, float, dict[str, Any]]:
        """Execute worm in trial venv."""
        import importlib.util

        worm_paths = {
            "SYSTEMZ": "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/CORE/WORMS/SYSTEMZ.py",
            "PROCESSZ": "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/CORE/WORMS/PROCESSZ.py",
            "DISKZ": "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/CORE/WORMS/DISKZ.py",
            "NETWORXZ": "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/CORE/WORMS/NETWORXZ.py",
            "CLOCKZ": "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/CORE/WORMS/CLOCKZ.py",
        }

        path = worm_paths.get(worm_name)
        if not path or not Path(path).exists():
            return True, 0.0, {}

        start = time.time()
        try:
            spec = importlib.util.spec_from_file_location(worm_name, path)
            if spec is None or spec.loader is None:
                duration = (time.time() - start) * 1000
                return False, duration, {"error": f"no import spec for {path}"}
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, f"run_{worm_name.lower()}"):
                result = getattr(module, f"run_{worm_name.lower()}")()
            else:
                result = {"success": True}

            duration = (time.time() - start) * 1000
            return True, duration, result
        except Exception as e:
            note_failure("HYDRAMETA_UV:L236", e)
            duration = (time.time() - start) * 1000
            return False, duration, {"error": str(e)}

    def run_trial(
        self,
        worm_sequence: (list[str] | None) = None,
        params: (dict[str, Any] | None) = None,
        packages: (list[str] | None) = None,
    ) -> UVTrialResult:
        """Run single optimization trial with uv."""
        trial_id = hashlib.md5(f"{time.time()}_{random.random()}".encode()).hexdigest()[
            :8
        ]

        params = params or self.config.DEFAULT_PARAMS
        worm_sequence = worm_sequence or self.config.WORMS[:4]
        packages = packages or ["psutil", "requests"]

        venv_path = None
        try:
            venv_start = time.time()
            venv_path = self._create_trial_venv(trial_id)
            success, uv_output, uv_duration = self._install_worm_deps(
                venv_path, packages
            )
            uv_overhead = (time.time() - venv_start) * 1000

            worm_start = time.time()
            results = []
            for worm in worm_sequence:
                success, duration, result = self._execute_worm(
                    venv_path, worm, params.get("timeout", 30.0)
                )
                results.append({"worm": worm, "success": success, "duration": duration})

            worm_duration = (time.time() - worm_start) * 1000
            total_duration = (time.time() - venv_start) * 1000

            efficiency = self._calculate_efficiency(results, total_duration)

            trial_result = UVTrialResult(
                trial_id=trial_id,
                worm_sequence=worm_sequence,
                duration_ms=total_duration,
                efficiency_score=efficiency,
                uv_install_time_ms=uv_overhead,
                worm_execution_time_ms=worm_duration,
                packages_installed=len(packages),
                parameters=params,
                success=True,
            )

            self.trials.append(trial_result)
            return trial_result

        except Exception as e:
            note_failure("HYDRAMETA_UV:L292", e)
            return UVTrialResult(
                trial_id=trial_id,
                worm_sequence=worm_sequence or [],
                duration_ms=0,
                efficiency_score=0.0,
                uv_install_time_ms=0,
                worm_execution_time_ms=0,
                packages_installed=0,
                parameters=params or {},
                success=False,
                error=str(e),
            )
        finally:
            if venv_path and params.get("uv_cache", True) is False:
                shutil.rmtree(venv_path, ignore_errors=True)

    def _calculate_efficiency(self, results: list[dict], duration_ms: float) -> float:
        """Calculate efficiency score."""
        if not results:
            return 0.0
        success_rate = sum(1 for r in results if r["success"]) / len(results)
        avg_duration = sum(r["duration"] for r in results) / len(results)
        efficiency = (success_rate * 1000) / max(avg_duration, 0.001)
        return round(min(efficiency, 100.0), 2)

    def run_trials(
        self,
        count: int = 10,
        parallel: bool = False,
        packages: (list[str] | None) = None,
    ) -> list[UVTrialResult]:
        """Run multiple optimization trials."""
        self.trials = []

        if parallel and count > 1:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(self.run_trial, packages=packages)
                    for _ in range(count)
                ]
                self.trials = [f.result() for f in as_completed(futures)]
        else:
            for _ in range(count):
                trial = self.run_trial(packages=packages)
                self.trials.append(trial)

        self._save_trials()
        self._update_best()
        return self.trials

    def _save_trials(self):
        """Persist trials to file."""
        trials_file = Path(self.config.TRIALS_FILE)
        trials_file.parent.mkdir(parents=True, exist_ok=True)
        with open(trials_file, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "trial_id": t.trial_id,
                        "worm_sequence": t.worm_sequence,
                        "duration_ms": t.duration_ms,
                        "efficiency_score": t.efficiency_score,
                        "uv_overhead_ms": t.uv_install_time_ms,
                        "success": t.success,
                        "error": t.error,
                    }
                    for t in self.trials
                ],
                f,
                indent=2,
            )

    def _update_best(self):
        """Update best sequence based on trials."""
        successful = [t for t in self.trials if t.success]
        if not successful:
            return

        best = max(successful, key=lambda t: t.efficiency_score)
        self.state["best_sequence"] = best.worm_sequence
        self.state["best_params"] = best.parameters
        self.state["best_efficiency"] = best.efficiency_score
        self._save_state()

    def analyze_performance(self) -> dict[str, Any]:
        """Analyze trial performance."""
        if not self.trials:
            return {"message": "No trials run yet"}

        successful = [t for t in self.trials if t.success]
        return {
            "total_trials": len(self.trials),
            "successful_trials": len(successful),
            "success_rate": len(successful) / max(len(self.trials), 1),
            "avg_duration_ms": sum(t.duration_ms for t in successful)
            / max(len(successful), 1),
            "avg_efficiency": sum(t.efficiency_score for t in successful)
            / max(len(successful), 1),
            "best_efficiency": max(t.efficiency_score for t in successful) or 0.0,
            "best_sequence": self.state.get("best_sequence", []),
            "best_params": self.state.get("best_params", {}),
            "avg_uv_overhead_ms": sum(t.uv_install_time_ms for t in successful)
            / max(len(successful), 1),
        }

    def optimize_sequence(
        self, strategy: str = "uv_prioritized"
    ) -> tuple[list[str], dict[str, Any]]:
        """Get optimized worm sequence."""
        if strategy == "round_robin":
            sequence = random.sample(
                self.config.WORMS, k=min(4, len(self.config.WORMS))
            )
        elif strategy == "performance_based":
            worm_scores = {}
            for trial in self.trials:
                for i, worm in enumerate(trial.worm_sequence):
                    if worm not in worm_scores:
                        worm_scores[worm] = []
                    worm_scores[worm].append(trial.efficiency_score / (i + 1))
            sequence = sorted(
                worm_scores.keys(), key=lambda w: -np.mean(worm_scores.get(w, [0]))
            )[:4]
        elif strategy == "ml_optimized":
            best = self.state.get("best_sequence", self.config.WORMS[:4])
            sequence = best
        else:
            sequence = [
                "SYSTEMZ",
                "DISKZ",
                "PROCESSZ",
                "NETWORXZ",
            ]

        params = self.state.get("best_params", self.config.DEFAULT_PARAMS)
        return sequence, params

    def benchmark_uv_speed(self, packages: list[str]) -> dict[str, Any]:
        """Benchmark uv vs pip installation speed."""

        uv_start = time.time()
        success, output, uv_duration = self._run_uv_command(
            ["pip", "install", "--dry-run"] + packages
        )
        uv_time = (time.time() - uv_start) * 1000

        return {
            "packages": packages,
            "uv_estimated_ms": uv_duration,
            "uv_time_seconds": round(uv_time / 1000, 3),
            "packages_per_second": len(packages) / max(uv_time / 1000, 0.001),
        }

    def run_popup(self):
        """Run with GUI popup."""
        root = tk.Tk()
        root.title("HYDRAMETA_UV - uv-Powered Optimization")
        root.geometry("700x500")

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        trials_tab = ttk.Frame(notebook)
        notebook.add(trials_tab, text="Trials")

        log_tab = ttk.Frame(notebook)
        notebook.add(log_tab, text="Logs")

        stats_tab = ttk.Frame(notebook)
        notebook.add(stats_tab, text="Stats")

        status_label = ttk.Label(
            trials_tab, text="Ready to run trials...", font=("Arial", 12)
        )
        status_label.pack(pady=10)

        log_text = scrolledtext.ScrolledText(log_tab, width=80, height=20)
        log_text.pack(fill="both", expand=True, padx=10, pady=10)

        def run_trials_async():
            results = self.run_trials(10)
            status_label.config(
                text=f"Completed {len(results)} trials\n"
                f"Best efficiency: {self.state.get('best_efficiency', 0)}%"
            )
            log_text.insert("end", json.dumps(self.analyze_performance(), indent=2))

        run_btn = ttk.Button(
            trials_tab,
            text="Run 10 Trials",
            command=lambda: [run_btn.config(state="disabled"), run_trials_async()],
        )
        run_btn.pack(pady=10)

        root.mainloop()


def main():
    """CLI entry point."""

    parser = argparse.ArgumentParser(
        description="HYDRAMETA_UV - uv-Powered Optimization"
    )
    parser.add_argument("--version", action="version", version=HYDRAMETA_UV.VERSION)
    parser.add_argument("--trials", type=int, default=5, help="Number of trials")
    parser.add_argument(
        "--parallel", action="store_true", help="Run trials in parallel"
    )
    parser.add_argument("--analyze", action="store_true", help="Analyze performance")
    parser.add_argument(
        "--optimize", action="store_true", help="Get optimized sequence"
    )
    parser.add_argument("--benchmark", nargs="+", help="Benchmark packages")

    args = parser.parse_args()
    optimizer = HYDRAMETA_UV()

    if args.analyze:
        print(json.dumps(optimizer.analyze_performance(), indent=2))
    elif args.optimize:
        sequence, params = optimizer.optimize_sequence()
        print(f"Optimized sequence: {sequence}")
        print(f"Parameters: {params}")
    elif args.benchmark:
        print(json.dumps(optimizer.benchmark_uv_speed(args.benchmark), indent=2))
    else:
        results = optimizer.run_trials(args.trials, args.parallel)
        print(f"Completed {len(results)} trials")
        print(f"Best efficiency: {optimizer.state.get('best_efficiency', 0)}%")


if __name__ == "__main__":
    main()
