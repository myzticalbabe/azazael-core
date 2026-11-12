# coding: utf-8
"""
HYDRAMETA - Hydratic Meta-Worms with HYDRA ML Optimization

Advanced meta-worm system that uses HYDRA cluster ML for optimization trials
with persistence and alternating worms for intelligent systems management.

Features:
- HYDRA ML cluster integration for optimization
- Alternating worm execution strategies
- Persistence state management
- ML-driven performance optimization
- Trial-based parameter tuning
- Adaptive worm selection

Usage:
    >>> import hydrometa
    >>> hydrometa.run_optimized()      # Run ML-optimized worms
    >>> hydrometa.run_trials(10)       # Run 10 optimization trials
    >>> hydrometa.analyze_performance() # Analyze performance
    >>> hydrometa.run_popup()          # Run with popup
"""

import json
import time
import random
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional
from dataclasses import dataclass, field
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# NOTE (3.14t port): tkinter imports live inside run_popup() — a top-level
# import pointlessly hard-requires Tk on headless interpreters.


@dataclass
class TrialResult:
    """Result from an optimization trial."""

    trial_id: str
    worm_sequence: list[str]
    duration_ms: float
    efficiency_score: float
    parameters: dict[str, Any]
    metrics: dict[str, float]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class WormPerformance:
    """Performance metrics for a worm."""

    worm_name: str
    avg_duration_ms: float
    success_rate: float
    efficiency_score: float
    execution_count: int
    last_executed: str


class HYDRAMETAConfig:
    """Configuration for HYDRAMETA."""

    TARGET_FOLDERS = [
        "C:/Users/clint/Desktop/Azazael",
        "C:/Users/clint/Desktop/HYDRA.py",
        "C:/Users/clint/Desktop/HORUS.py",
        "C:/Users/clint/Desktop/TARBABY.py",
        "C:/Users/clint/Desktop/POINDEXTER",
        "C:/Users/clint/Desktop/PERSEPHONE",
        "C:/Users/clint/Desktop/WORMZ",
        "C:/Users/clint/Desktop/METAWORMS",
        "C:/Users/clint/Desktop/TERENCE",
    ]

    # State lives with the repo (2026-09-09 3.14t port): the old
    # C:/Users/clint/Desktop/METAWORMS tree never existed on this machine,
    # so every save failed silently and no trial ever persisted.
    _ROOT = Path(__file__).resolve().parent.parent
    OUTPUT_PATH = str(_ROOT / "state" / "hydrometa")
    STATE_FILE = str(_ROOT / "state" / "hydrometa" / "state.json")
    TRIALS_FILE = str(_ROOT / "state" / "hydrometa" / "trials.json")

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

    ALTERNATING_STRATEGIES = [
        "round_robin",
        "performance_based",
        "resource_aware",
        "randomized",
        "ml_optimized",
    ]

    DEFAULT_PARAMS = {
        "interval": 1.0,
        "parallel": False,
        "timeout": 30,
        "retry": 1,
        "priority": "normal",
    }

    NUM_TRIALS = 10
    POPULATION_SIZE = 10
    MUTATION_RATE = 0.2


class HYDRAOptimizer:
    """HYDRA ML optimizer for worm parameters."""

    def __init__(self, config=None):
        self.config = config or HYDRAMETAConfig()
        # 3.14t port: own lock + own RNG. The old code mutated shared
        # state lock-free and reseeded the GLOBAL random module per call —
        # benign under the 3.13 GIL-era single-threaded use, a race (and a
        # determinism-poison for co-tenants like evolution's seeded evals)
        # under free-threaded parallelism. RLock: get_best_params() can
        # trigger initialize+evolve while holding the lock.
        self._lock = threading.RLock()
        self._rng = random.Random(time.time_ns() ^ id(self))
        self.population: list[dict] = []
        self.best_params: dict = {}
        self.best_score: float = 0.0
        self.trial_history: list[TrialResult] = []

    def _generate_params(self, seed: Optional[int] = None) -> dict[str, Any]:
        """Generate random parameters (instance RNG; seed arg kept for API)."""
        rng = random.Random(seed) if seed is not None else self._rng
        return {
            "interval": round(rng.uniform(0.1, 5.0), 2),
            "parallel": rng.choice([True, False]),
            "timeout": rng.randint(5, 60),
            "retry": rng.randint(0, 3),
            "priority": rng.choice(["low", "normal", "high"]),
            "batch_size": rng.randint(1, 20),
            "workers": rng.randint(1, 8),
        }

    def _fitness(self, params: dict, worms: list[str]) -> float:
        """Calculate fitness score for parameters."""
        score = 100.0
        score -= abs(params.get("interval", 1) - 1.0) * 10
        score -= params.get("timeout", 30) * 0.5
        score += params.get("retry", 0) * 2
        if params.get("parallel"):
            score += 5
        if params.get("priority") == "high":
            score += 3
        return max(0, min(100, score))

    def initialize_population(self, size: Optional[int] = None):
        """Initialize population for optimization."""
        size = size or self.config.POPULATION_SIZE
        pop = []
        for i in range(size):
            params = self._generate_params(seed=time.time_ns() + i)
            params["_score"] = self._fitness(params, [])
            pop.append(params)
        with self._lock:
            self.population = pop
            self.best_params = self.population[0].copy()
            self.best_score = self.best_params["_score"]

    def evolve(self, num_generations: int = 5):
        """Evolve population through generations."""
        for gen in range(num_generations):
            with self._lock:
                current = list(self.population)
            new_population = []
            for _ in range(len(current)):
                parent = self._rng.choice(current)
                child = self._mutate(parent)
                child["_score"] = self._fitness(child, [])
                new_population.append(child)
            with self._lock:
                self.population = sorted(
                    current + new_population,
                    key=lambda x: x.get("_score", 0),
                    reverse=True,
                )[: len(current)]
                if self.population[0].get("_score", 0) > self.best_score:
                    self.best_params = self.population[0].copy()
                    self.best_score = self.population[0]["_score"]

    def _mutate(self, params: dict) -> dict:
        """Mutate parameters."""
        child = params.copy()
        rng, rate = self._rng, self.config.MUTATION_RATE
        if rng.random() < rate:
            child["interval"] = round(
                child.get("interval", 1) + rng.uniform(-0.5, 0.5), 2
            )
        if rng.random() < rate:
            child["timeout"] = max(
                5, child.get("timeout", 30) + rng.randint(-10, 10)
            )
        if rng.random() < rate:
            child["retry"] = max(
                0, min(5, child.get("retry", 0) + rng.randint(-1, 1))
            )
        if rng.random() < rate:
            child["parallel"] = not child.get("parallel", False)
        if rng.random() < rate:
            child["priority"] = rng.choice(["low", "normal", "high"])
        if rng.random() < rate:
            child["batch_size"] = max(
                1, min(50, child.get("batch_size", 10) + rng.randint(-5, 5))
            )
        return child

    def record_trial(self, result: TrialResult) -> None:
        """Thread-safe history append (3.14t port)."""
        with self._lock:
            self.trial_history.append(result)

    def run_trial(
        self, trial_id: str, worm_sequence: list[str], params: dict,
        executor=None,
    ) -> TrialResult:
        """Run a single optimization trial.

        3.14t port: with executor(worm, params) -> {success, duration_ms,
        ...} the trial MEASURES real executions; without one it falls back
        to the legacy simulated estimates (flagged simulated=True) so
        direct optimizer use keeps working. HYDRAMETA.run_trials always
        passes its real executor.
        """
        start = time.time()
        if executor is None:
            duration = self._rng.uniform(100, 500) * len(worm_sequence)
            efficiency = self._fitness(params, worm_sequence)
            metrics = {
                "cpu_usage": self._rng.uniform(10, 50),
                "memory_usage": self._rng.uniform(20, 60),
                "disk_io": self._rng.uniform(5, 30),
                "success_rate": self._rng.uniform(0.8, 1.0),
                "simulated": True,
            }
        else:
            effs, succs, total_ms = [], 0, 0.0
            for worm in worm_sequence:
                try:
                    r = executor(worm, params) or {}
                except Exception as exc:
                    note_failure("HYDRAMETA:L260", exc)
                    r = {"success": False, "duration_ms": 0.0}
                ok = bool(r.get("success", False))
                ms = float(r.get("duration_ms", 0.0) or 0.0)
                succs += 1 if ok else 0
                total_ms += ms
                effs.append((100.0 - min(ms, 1000.0) / 10.0) if ok else 0.0)
            duration = (time.time() - start) * 1000.0
            efficiency = round(sum(effs) / len(effs), 3) if effs else 0.0
            metrics = {
                "success_rate": round(succs / len(worm_sequence), 3) if worm_sequence else 0.0,
                "measured": True,
            }

        result = TrialResult(
            trial_id=trial_id,
            worm_sequence=worm_sequence,
            duration_ms=duration,
            efficiency_score=efficiency,
            parameters=params,
            metrics=metrics,
        )

        self.record_trial(result)
        return result

    def get_best_params(self) -> dict:
        """Get best parameters from optimization."""
        with self._lock:
            empty = not self.best_params
        if empty:
            self.initialize_population()
            self.evolve()
        with self._lock:
            return {k: v for k, v in self.best_params.items() if not k.startswith("_")}

    def get_trial_history(self) -> list[dict]:
        """Get trial history."""
        return [
            {
                "trial_id": t.trial_id,
                "worms": t.worm_sequence,
                "duration_ms": t.duration_ms,
                "efficiency": t.efficiency_score,
                "timestamp": t.timestamp,
            }
            for t in self.trial_history
        ]


class PersistenceManager:
    """Persistence manager for HYDRAMETA state."""

    def __init__(self, state_file: str, trials_file: str):
        # RLock (3.14t port): update_worm_performance -> save_state nests.
        self._lock = threading.RLock()
        self.state_file = Path(state_file)
        self.trials_file = Path(trials_file)
        self.state: dict = self._load_state()
        self.trials: list[dict] = self._load_trials()

    def _load_state(self) -> dict:
        """Load persistent state."""
        try:
            if self.state_file.exists():
                return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception as exc:
            note_failure("HYDRAMETA:L326", exc)
        return {
            "version": "1.0.0",
            "last_run": None,
            "total_runs": 0,
            "best_efficiency": 0.0,
            "worm_performance": {},
            "preferred_sequence": [],
            "ml_params": HYDRAMETAConfig.DEFAULT_PARAMS,
        }

    def _load_trials(self) -> list[dict]:
        """Load trial history."""
        try:
            if self.trials_file.exists():
                return json.loads(self.trials_file.read_text(encoding="utf-8"))
        except Exception as exc:
            note_failure("HYDRAMETA:L343", exc)
        return []

    def save_state(self, state: dict):
        """Save state (creates parent dirs; 3.14t port added parents=True —
        the old exist_ok-only call failed on nested missing trees)."""
        try:
            with self._lock:
                self.state_file.parent.mkdir(parents=True, exist_ok=True)
                self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
                self.state = state
        except Exception as e:
            print(f"[HYDRAMETA] State save error: {e}")

    def save_trials(self, trials: list[dict]):
        """Save trials."""
        try:
            with self._lock:
                self.trials_file.parent.mkdir(parents=True, exist_ok=True)
                self.trials_file.write_text(json.dumps(trials, indent=2), encoding="utf-8")
                self.trials = trials
        except Exception as e:
            print(f"[HYDRAMETA] Trials save error: {e}")

    def get_state(self) -> dict:
        """Get current state."""
        return self.state

    def update_worm_performance(self, worm_name: str, metrics: dict):
        """Update worm performance metrics (locked; nests save_state)."""
        with self._lock:
            if "worm_performance" not in self.state:
                self.state["worm_performance"] = {}
            if worm_name not in self.state["worm_performance"]:
                self.state["worm_performance"][worm_name] = {
                    "executions": 0,
                    "avg_duration": 0,
                    "success_rate": 1.0,
                    "efficiency": 0,
                }
            wp = self.state["worm_performance"][worm_name]
            wp["executions"] += 1
            wp["avg_duration"] = (
                wp["avg_duration"] * (wp["executions"] - 1) + metrics.get("duration", 0)
            ) / wp["executions"]
            wp["success_rate"] = metrics.get("success_rate", wp["success_rate"])
            wp["efficiency"] = metrics.get("efficiency", wp["efficiency"])
            self.save_state(self.state)

    def record_run(self) -> None:
        """Locked total_runs increment (3.14t port: read-modify-write)."""
        with self._lock:
            self.state["last_run"] = datetime.now().isoformat()
            self.state["total_runs"] = self.state.get("total_runs", 0) + 1
            self.save_state(self.state)

    def get_best_sequence(self) -> list[str]:
        """Get best worm sequence."""
        return self.state.get("preferred_sequence", HYDRAMETAConfig.WORMS)

    def set_best_sequence(self, sequence: list[str]):
        """set best worm sequence."""
        self.state["preferred_sequence"] = sequence
        self.save_state(self.state)


class HYDRAMETA:
    """Main HYDRAMETA - Hydratic Meta-Worms with HYDRA ML."""

    VERSION = "1.0.0"

    def __init__(self):
        self.config = HYDRAMETAConfig()
        self.optimizer = HYDRAOptimizer(self.config)
        self.persistence = PersistenceManager(
            self.config.STATE_FILE, self.config.TRIALS_FILE
        )
        # 3.14t port: own lock + RNG (running flag and strategy sampling
        # were shared unsynchronized globals-adjacent state).
        self._lock = threading.RLock()
        self._rng = random.Random(time.time_ns() ^ id(self))
        self.running = False

    def _import_worm_module(self, name: str):
        """Resolve a legacy worm module: top-level, then CORE.WORMS.

        3.14t port: bare `import X` only works from legacy CWDs; the
        fallback keeps headless/scheduled use working. None = unresolvable.
        """
        try:
            return __import__(name)
        except ImportError as exc:
            note_failure("HYDRAMETA:L435", exc)
        try:
            import importlib
            return importlib.import_module(f"CORE.WORMS.{name}")
        except ImportError as exc:
            note_failure("HYDRAMETA:L440", exc)
            return None

    def _select_worms_strategy(
        self, strategy: str = "ml_optimized", count: int = 5
    ) -> list[str]:
        """Select worms based on strategy."""
        if strategy == "round_robin":
            return HYDRAMETAConfig.WORMS[:count]
        elif strategy == "performance_based":
            perf = self.persistence.state.get("worm_performance", {})
            sorted_worms = sorted(
                perf.items(), key=lambda x: x[1].get("efficiency", 0), reverse=True
            )
            return [w[0] for w in sorted_worms[:count]] or HYDRAMETAConfig.WORMS[:count]
        elif strategy == "randomized":
            return random.sample(
                HYDRAMETAConfig.WORMS, min(count, len(HYDRAMETAConfig.WORMS))
            )
        elif strategy == "ml_optimized":
            return self.persistence.get_best_sequence()[:count]
        else:
            return HYDRAMETAConfig.WORMS[:count]

    def _execute_worm(self, worm_name: str, params: dict) -> dict:
        """Execute a single worm."""
        start = time.time()
        success = True
        error = None

        try:
            if worm_name == "SYSTEMZ":
                mod = self._import_worm_module("SYSTEMZ")
                result = mod.SYSTEMZ().get_status().__dict__ if mod else {"error": "unresolvable module"}
            elif worm_name == "PROCESSZ":
                mod = self._import_worm_module("PROCESSZ")
                result = mod.PROCESSZ().get_summary() if mod else {"error": "unresolvable module"}
            elif worm_name == "DISKZ":
                mod = self._import_worm_module("DISKZ")
                result = mod.DISKZ().get_summary() if mod else {"error": "unresolvable module"}
            elif worm_name == "NETWORXZ":
                mod = self._import_worm_module("NETWORXZ")
                result = mod.NETWORXZ().get_summary() if mod else {"error": "unresolvable module"}
            elif worm_name == "CLOCKZ":
                mod = self._import_worm_module("CLOCKZ")
                result = mod.CLOCKZ().get_uptime() if mod else {"error": "unresolvable module"}
            elif worm_name == "OPTIMIZ":
                mod = self._import_worm_module("OPTIMIZ")
                result = mod.OPTIMIZ().analyze() if mod else {"error": "unresolvable module"}
            elif worm_name == "HEALTHZ":
                mod = self._import_worm_module("HEALTHZ")
                result = mod.HEALTHZ().check() if mod else {"error": "unresolvable module"}
            else:
                result = {"status": "skipped", "worm": worm_name}
        except Exception as e:
            note_failure("HYDRAMETA:L494", e)
            success = False
            error = str(e)
            result = {"error": error}

        # A known worm whose module can't be resolved is a FAILURE, not a
        # skip — otherwise missing modules inflate the measured success
        # rate the optimizer learns from (3.14t port honesty fix).
        # Other names stay neutral skips (not dispatchable here).
        if success and isinstance(result, dict) and result.get("error"):
            success = False
            error = str(result["error"])[:300]

        duration = (time.time() - start) * 1000

        self.persistence.update_worm_performance(
            worm_name,
            {
                "duration": duration,
                "success": success,
                "success_rate": 1.0 if success else 0.0,
                "efficiency": max(0, 100 - duration / 10),
            },
        )

        return {
            "worm": worm_name,
            "success": success,
            "duration_ms": duration,
            "result": str(result)[:200],
            "error": error,
        }

    def run_optimized(
        self, strategy: str = "ml_optimized", count: int = 5, params: Optional[dict] = None
    ) -> dict:
        """Run worms with optimized parameters."""
        with self._lock:
            self.running = True
        start_time = time.time()

        params = params or self.optimizer.get_best_params()
        worms = self._select_worms_strategy(strategy, count)

        results = []
        for worm in worms:
            if not self.running:
                break
            result = self._execute_worm(worm, params)
            results.append(result)

        duration = (time.time() - start_time) * 1000
        success_count = sum(1 for r in results if r["success"])

        self.persistence.record_run()

        return {
            "strategy": strategy,
            "worms": worms,
            "params": params,
            "results": results,
            "duration_ms": duration,
            "success_rate": success_count / len(results) if results else 0,
        }

    def run_trials(self, num_trials: Optional[int] = None) -> list[TrialResult]:
        """Run optimization trials with REAL measured executions.

        3.14t port: trials execute the worm sequence through _execute_worm
        and score measured success/duration instead of the legacy random
        estimates. Strategy/count sampling uses the instance RNG.
        """
        num_trials = num_trials or self.config.NUM_TRIALS
        self.optimizer.initialize_population()

        results = []
        for i in range(num_trials):
            trial_id = f"trial_{i + 1}_{int(time.time())}"
            worms = self._select_worms_strategy(
                self._rng.choice(HYDRAMETAConfig.ALTERNATING_STRATEGIES),
                self._rng.randint(3, 8),
            )
            params = self._generate_optimized_params()
            result = self.optimizer.run_trial(
                trial_id, worms, params, executor=self._execute_worm)
            results.append(result)

        self.optimizer.evolve(num_generations=3)

        trial_data = self.optimizer.get_trial_history()
        self.persistence.save_trials(trial_data)

        if trial_data:
            best = max(trial_data, key=lambda x: x.get("efficiency", 0))
            self.persistence.set_best_sequence(best.get("worms", HYDRAMETAConfig.WORMS))

        return results

    def _generate_optimized_params(self) -> dict:
        """Generate optimized parameters from best practices."""
        rng = self._rng
        return {
            "interval": round(rng.uniform(0.5, 2.0), 2),
            "parallel": rng.choice([True, False]),
            "timeout": rng.randint(10, 30),
            "retry": rng.randint(1, 2),
            "priority": "normal",
            "batch_size": rng.randint(5, 15),
            "workers": rng.randint(2, 4),
        }

    def analyze_performance(self) -> dict:
        """Analyze worm performance."""
        perf = self.persistence.state.get("worm_performance", {})
        trials = self.persistence.trials

        return {
            "total_runs": self.persistence.state.get("total_runs", 0),
            "last_run": self.persistence.state.get("last_run"),
            "worm_performance": perf,
            "total_trials": len(trials),
            "best_efficiency": max([t.get("efficiency", 0) for t in trials])
            if trials
            else 0,
            "recommended_sequence": self.persistence.get_best_sequence(),
            "ml_params": self.optimizer.get_best_params(),
        }

    def get_status(self) -> dict:
        """Get HYDRAMETA status."""
        return {
            "version": self.VERSION,
            "running": self.running,
            "total_runs": self.persistence.state.get("total_runs", 0),
            "last_run": self.persistence.state.get("last_run"),
            "best_params": self.optimizer.get_best_params(),
            "trial_count": len(self.optimizer.trial_history),
        }


def run_popup():
    """Run HYDRAMETA with popup window."""
    import tkinter as tk
    from tkinter import ttk, scrolledtext
    root = tk.Tk()
    root.title("HYDRAMETA - Hydratic Meta-Worms with HYDRA ML")
    root.geometry("1100x850")
    root.configure(bg="#1a1a2e")

    hydrometa = HYDRAMETA()

    def log(msg, level="INFO"):
        colors = {
            "OK": "#00ff88",
            "FAIL": "#ff4444",
            "WARN": "#ffaa00",
            "INFO": "#00aaff",
            "ML": "#ff00ff",
        }
        timestamp = datetime.now().strftime("%H:%M:%S")
        text_area.insert("end", f"[{timestamp}] [{level}] {msg}\n")
        text_area.see("end")
        root.update_idletasks()

    header = tk.Frame(root, bg="#16213e", pady=15)
    header.pack(fill="x")

    tk.Label(
        header,
        text="HYDRAMETA - Hydratic Meta-Worms",
        font=("Consolas", 28, "bold"),
        bg="#16213e",
        fg="#00d4ff",
    ).pack()

    tk.Label(
        header,
        text="HYDRA ML Optimization • Alternating Worms • Persistence",
        font=("Consolas", 12),
        bg="#16213e",
        fg="#7c3aed",
    ).pack()

    status_label = tk.Label(
        root,
        text="Ready",
        font=("Consolas", 14),
        bg="#1a1a2e",
        fg="#00ff88",
    )
    status_label.pack(pady=5)

    control_frame = tk.Frame(root, bg="#1a1a2e", pady=10)
    control_frame.pack(fill="x", padx=20)

    tk.Label(
        control_frame,
        text="Strategy:",
        bg="#1a1a2e",
        fg="#00d4ff",
        font=("Consolas", 11),
    ).pack(side="left")

    strategy_var = tk.StringVar(value="ml_optimized")
    strategy_combo = ttk.Combobox(
        control_frame,
        textvariable=strategy_var,
        values=["round_robin", "performance_based", "randomized", "ml_optimized"],
        width=15,
        font=("Consolas", 10),
    )
    strategy_combo.pack(side="left", padx=10)

    tk.Label(
        control_frame, text="Worms:", bg="#1a1a2e", fg="#00d4ff", font=("Consolas", 11)
    ).pack(side="left", padx=(20, 0))

    count_var = tk.IntVar(value=5)
    count_spin = tk.Spinbox(
        control_frame,
        from_=1,
        to=16,
        textvariable=count_var,
        width=5,
        font=("Consolas", 10),
    )
    count_spin.pack(side="left", padx=10)

    progress_frame = tk.Frame(root, bg="#1a1a2e", pady=10)
    progress_frame.pack(fill="x", padx=20)

    progress = ttk.Progressbar(progress_frame, mode="determinate", length=1050)
    progress.pack(fill="x")

    progress_label = tk.Label(
        progress_frame,
        text="0 / 0",
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00ff88",
    )
    progress_label.pack()

    stats_frame = tk.Frame(root, bg="#1a1a2e", pady=5)
    stats_frame.pack(fill="x", padx=20)

    stats_label = tk.Label(
        stats_frame,
        text="Runs: 0 | Trials: 0 | Efficiency: 0%",
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00d4ff",
    )
    stats_label.pack()

    text_area = scrolledtext.ScrolledText(
        root, wrap="word", font=("Consolas", 10), bg="#0f0f1a", fg="#00ff88", height=35
    )
    text_area.pack(fill="both", expand=True, padx=20, pady=10)

    button_frame = tk.Frame(root, bg="#1a1a2e", pady=15)
    button_frame.pack(fill="x", padx=20)

    def run_optimized_worms():
        status_label.config(text="Running Optimized...")
        progress["value"] = 0
        text_area.delete("1.0", "end")

        log("=" * 60, "INFO")
        log("HYDRAMETA - ML Optimized Worm Execution", "INFO")
        log("=" * 60, "INFO")

        strategy = strategy_var.get()
        count = count_var.get()

        log(f"Strategy: {strategy} | Worms: {count}", "INFO")

        params = hydrometa.optimizer.get_best_params()
        log(f"ML Parameters: {params}", "ML")

        worms = hydrometa._select_worms_strategy(strategy, count)
        log(f"Worm Sequence: {worms}", "INFO")

        progress["value"] = 20
        progress_label.config(text="Executing worms...")
        root.update()

        results = hydrometa.run_optimized(strategy, count, params)

        log("\nExecution Results:", "INFO")
        for r in results["results"]:
            status = "OK" if r["success"] else "FAIL"
            log(
                f"  [{status}] {r['worm']}: {r['duration_ms']:.0f}ms",
                status if r["success"] else "FAIL",
            )

        progress["value"] = 80
        log("\nSummary:", "INFO")
        log(f"  Duration: {results['duration_ms']:.0f}ms", "INFO")
        log(f"  Success Rate: {results['success_rate'] * 100:.0f}%", "INFO")

        status = hydrometa.analyze_performance()
        log(f"  Total Runs: {status['total_runs']}", "INFO")

        progress["value"] = 100
        stats_label.config(
            text=f"Runs: {status['total_runs']} | Trials: {status['total_trials']} | Efficiency: {status['best_efficiency']:.0f}%"
        )
        status_label.config(text="Complete!")

    def run_optimization_trials():
        status_label.config(text="Running Trials...")
        progress["value"] = 0
        text_area.delete("1.0", "end")

        log("=" * 60, "INFO")
        log("HYDRAMETA - HYDRA ML Optimization Trials", "INFO")
        log("=" * 60, "INFO")

        num_trials = 10
        log(f"Running {num_trials} optimization trials...", "INFO")

        results = hydrometa.run_trials(num_trials)

        log("\nTrial Results:", "INFO")
        for r in results:
            log(
                f"  Trial {r.trial_id}: Efficiency={r.efficiency_score:.1f}% | Duration={r.duration_ms:.0f}ms",
                "INFO",
            )

        progress["value"] = 60
        root.update()

        best = max(results, key=lambda x: x.efficiency_score)
        log(f"\nBest Trial: {best.trial_id}", "OK")
        log(f"  Efficiency: {best.efficiency_score:.1f}%", "INFO")
        log(f"  Worm Sequence: {best.worm_sequence}", "INFO")

        params = hydrometa.optimizer.get_best_params()
        log(f"\nOptimized Parameters: {params}", "ML")

        progress["value"] = 100
        status = hydrometa.analyze_performance()
        stats_label.config(
            text=f"Runs: {status['total_runs']} | Trials: {len(results)} | Efficiency: {status['best_efficiency']:.0f}%"
        )
        status_label.config(text="Trials Complete!")

    def show_performance():
        status_label.config(text="Analyzing Performance...")
        text_area.delete("1.0", "end")

        log("=" * 60, "INFO")
        log("HYDRAMETA - Performance Analysis", "INFO")
        log("=" * 60, "INFO")

        status = hydrometa.analyze_performance()

        log("\nSystem Status:", "INFO")
        log(f"  Total Runs: {status['total_runs']}", "INFO")
        log(f"  Total Trials: {status['total_trials']}", "INFO")
        log(f"  Best Efficiency: {status['best_efficiency']:.1f}%", "OK")
        log(f"  Last Run: {status['last_run']}", "INFO")

        log("\nRecommended Worm Sequence:", "INFO")
        for i, worm in enumerate(status["recommended_sequence"], 1):
            log(f"  {i}. {worm}", "INFO")

        log("\nML Optimized Parameters:", "ML")
        for k, v in status["ml_params"].items():
            log(f"  {k}: {v}", "ML")

        perf = status.get("worm_performance", {})
        if perf:
            log("\nWorm Performance:", "INFO")
            for worm, metrics in sorted(
                perf.items(), key=lambda x: x[1].get("efficiency", 0), reverse=True
            )[:10]:
                log(
                    f"  {worm}: Efficiency={metrics.get('efficiency', 0):.0f}% | Executions={metrics.get('executions', 0)}",
                    "INFO",
                )

        status_label.config(text="Analysis Complete!")

    def on_optimized():
        for btn in [btn_run, btn_trials, btn_perf]:
            btn.config(state="disabled")
        root.update()
        try:
            run_optimized_worms()
        finally:
            for btn in [btn_run, btn_trials, btn_perf]:
                btn.config(state="normal")

    def on_trials():
        for btn in [btn_run, btn_trials, btn_perf]:
            btn.config(state="disabled")
        root.update()
        try:
            run_optimization_trials()
        finally:
            for btn in [btn_run, btn_trials, btn_perf]:
                btn.config(state="normal")

    def on_perf():
        for btn in [btn_run, btn_trials, btn_perf]:
            btn.config(state="disabled")
        root.update()
        try:
            show_performance()
        finally:
            for btn in [btn_run, btn_trials, btn_perf]:
                btn.config(state="normal")

    btn_run = tk.Button(
        button_frame,
        text="RUN OPTIMIZED",
        command=on_optimized,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#00ff88",
        width=16,
    )
    btn_run.pack(side="left", padx=10)

    btn_trials = tk.Button(
        button_frame,
        text="RUN TRIALS",
        command=on_trials,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#ff00ff",
        width=14,
    )
    btn_trials.pack(side="left", padx=10)

    btn_perf = tk.Button(
        button_frame,
        text="ANALYZE",
        command=on_perf,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#00d4ff",
        width=12,
    )
    btn_perf.pack(side="left", padx=10)

    close_btn = tk.Button(
        button_frame,
        text="CLOSE",
        command=root.destroy,
        font=("Consolas", 13, "bold"),
        bg="#ff4444",
        fg="#ffffff",
        width=10,
    )
    close_btn.pack(side="right", padx=10)

    log("HYDRAMETA Ready", "INFO")
    log("Configure strategy and click RUN OPTIMIZED or RUN TRIALS", "INFO")

    root.mainloop()


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(
        description="HYDRAMETA - Hydratic Meta-Worms with HYDRA ML"
    )
    parser.add_argument("--run", action="store_true", help="Run optimized worms")
    parser.add_argument(
        "--trials", type=int, help="Run N optimization trials", metavar="N"
    )
    parser.add_argument(
        "--strategy",
        type=str,
        help="Strategy: round_robin, performance_based, randomized, ml_optimized",
    )
    parser.add_argument("--worms", type=int, help="Number of worms to run")
    parser.add_argument("--analyze", action="store_true", help="Analyze performance")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--popup", action="store_true", help="Show popup window")

    args = parser.parse_args()

    hm = HYDRAMETA()

    if args.popup:
        run_popup()
    elif args.status:
        print(json.dumps(hm.get_status(), indent=2))
    elif args.analyze:
        print(json.dumps(hm.analyze_performance(), indent=2))
    elif args.trials:
        results = hm.run_trials(args.trials)
        print(f"Ran {len(results)} trials")
        print(f"Best efficiency: {max(r.efficiency_score for r in results):.1f}%")
    elif args.run:
        strategy = args.strategy or "ml_optimized"
        count = args.worms or 5
        result = hm.run_optimized(strategy, count)
        print(json.dumps(result, indent=2, default=str))
    else:
        print("HYDRAMETA - Hydratic Meta-Worms with HYDRA ML")
        print()
        print("Usage:")
        print("  python HYDRAMETA.py --run              Run optimized worms")
        print("  python HYDRAMETA.py --trials N         Run N optimization trials")
        print("  python HYDRAMETA.py --strategy round_robin   Select strategy")
        print("  python HYDRAMETA.py --worms 5           Number of worms")
        print("  python HYDRAMETA.py --analyze          Analyze performance")
        print("  python HYDRAMETA.py --status           Show status")
        print("  python HYDRAMETA.py --popup            Show popup window")
        print()
        print("Strategies:")
        print("  round_robin     - Sequential execution")
        print("  performance_based - Best performing first")
        print("  randomized      - Random selection")
        print("  ml_optimized    - HYDRA ML optimized")


if __name__ == "__main__":
    main()
