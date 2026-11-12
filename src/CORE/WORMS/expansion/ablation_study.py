# coding: utf-8
"""
Ablation Study Framework for Azazael Neural Architecture

Systematically evaluates component contributions by running controlled experiments
with individual components enabled/disabled. Integrates with NeuralPipeline and
existing training infrastructure.

Usage:
    from CORE.WORMS.expansion.ablation_study import AblationStudy, AblationConfig
    
    study = AblationStudy()
    results = study.run_full_ablation(
        source="SystemZ",
        components_to_ablate=["llm_augmentation", "data_split", "feature_engineering", "parallel_training"]
    )
"""

import itertools
import json
import os
import statistics
import time
import threading
from collections import defaultdict
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, Set
from dataclasses import dataclass, field, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from CORE.failure_ledger import note_failure  # type: ignore[assignment]
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:  # type: ignore[no-redef]
        pass

# EP455 lifecycle collaborator: exact Shapley marginal attribution.
# First-party, cycle-free (skill_library never imports this module);
# declared once here, not per call. None disables to the named gap.
try:
    from CORE.skill_library import shapley_marginal as _shapley_marginal
    _SHAPLEY_OK = True
except ImportError as exc:
    note_failure("ablation_study:shapley-import", exc)
    _shapley_marginal = None  # type: ignore
    _SHAPLEY_OK = False


@dataclass
class AblationConfig:
    """Configuration for a single ablation experiment."""
    name: str
    description: str
    components_enabled: Dict[str, bool]  # component_name -> enabled
    baseline_config: Dict[str, Any]  # Base NeuralPipeline config
    train_kwargs: Dict[str, Any] = field(default_factory=dict)
    repeats: int = 3  # Number of repeats for statistical significance
    timeout_seconds: int = 300


@dataclass
class AblationResult:
    """Result of a single ablation experiment run."""
    config_name: str
    run_id: int
    components_enabled: Dict[str, bool]
    metrics: Dict[str, Any]
    duration_ms: float
    status: str  # "success", "failed", "timeout"
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class AblationSummary:
    """Aggregated results across multiple runs of the same configuration."""
    config_name: str
    components_enabled: Dict[str, bool]
    n_runs: int
    successful_runs: int
    avg_duration_ms: float
    avg_metrics: Dict[str, float]
    std_metrics: Dict[str, float]
    metric_deltas: Dict[str, float]  # vs baseline
    significance: Dict[str, str]  # "significant", "marginal", "not_significant"


class AblationComponent:
    """Defines a single component that can be ablated."""
    
    def __init__(
        self,
        name: str,
        description: str,
        enable_fn: Callable[[Any, bool], None],
        affects: Optional[List[str]] = None,
    ):
        """
        Args:
            name: Unique component identifier
            description: Human-readable description
            enable_fn: Function(pipeline, enabled) -> modifies pipeline for this component
            affects: List of metric names this component primarily affects
        """
        self.name = name
        self.description = description
        self.enable_fn = enable_fn
        self.affects = affects or []


class AblationStudy:
    """
    Comprehensive ablation study framework for NeuralPipeline components.
    
    Supports:
    - Single component ablation (one-at-a-time removal)
    - Pairwise ablation (interaction effects)
    - Full factorial ablation (all combinations)
    - Statistical significance testing
    - Integration with existing NeuralPipeline infrastructure
    """
    
    # Core components that can be ablated
    CORE_COMPONENTS = {
        "llm_augmentation": AblationComponent(
            name="llm_augmentation",
            description="LLM-based reward shaping during training (6 inference APIs)",
            enable_fn=lambda pipeline, enabled: setattr(pipeline, "llm_augmentation", enabled),
            affects=["avg_reward_score", "convergence", "training_stability"],
        ),
        "data_split": AblationComponent(
            name="data_split",
            description="Temporal session-based data splitting with cross-validation",
            enable_fn=lambda pipeline, enabled: setattr(pipeline.data_pipe, "use_temporal_split", enabled),
            affects=["generalization", "overfitting", "test_accuracy"],
        ),
        "feature_engineering": AblationComponent(
            name="feature_engineering",
            description="SchemaAwareFeatureExtractor with typed feature extraction",
            enable_fn=lambda pipeline, enabled: setattr(pipeline.feature_ext, "use_schema", enabled),
            affects=["feature_dim", "feature_quality", "training_speed"],
        ),
        "parallel_training": AblationComponent(
            name="parallel_training",
            description="WorkStealingPool parallel execution for multi-agent training",
            enable_fn=lambda pipeline, enabled: setattr(pipeline, "use_parallel", enabled),
            affects=["training_time", "throughput", "resource_utilization"],
        ),
        "cross_api_analysis": AblationComponent(
            name="cross_api_analysis",
            description="Multi-provider convergence analysis for reward validation",
            enable_fn=lambda pipeline, enabled: setattr(pipeline, "run_cross_api", enabled),
            affects=["convergence_score", "reliability", "confidence"],
        ),
        "dead_letter_queue": AblationComponent(
            name="dead_letter_queue",
            description="Error recovery via DeadLetterQueue for failed runs",
            enable_fn=lambda pipeline, enabled: setattr(pipeline, "use_dlq", enabled),
            affects=["failure_recovery", "run_completion_rate"],
        ),
        "model_versioning": AblationComponent(
            name="model_versioning",
            description="ModelVersioner for tracking and rollback",
            enable_fn=lambda pipeline, enabled: setattr(pipeline, "use_versioning", enabled),
            affects=["reproducibility", "rollback_capability"],
        ),
        "prioritized_replay": AblationComponent(
            name="prioritized_replay",
            description="PrioritizedReplayBuffer with TD-error priority sampling",
            enable_fn=lambda pipeline, enabled: setattr(pipeline, "use_prioritized_replay", enabled),
            affects=["sample_efficiency", "convergence_speed"],
        ),
        "worm_features": AblationComponent(
            name="worm_features",
            description="FeatureEngineer worm-specific feature extraction",
            enable_fn=lambda pipeline, enabled: setattr(pipeline, "use_worm_features", enabled),
            affects=["worm_feature_dim", "rl_performance"],
        ),
    }
    
    def __init__(
        self,
        output_dir: str = "azazael/state/ablation_results",
        max_workers: int = 4,
    ):
        self.output_dir = output_dir
        self.max_workers = max_workers
        self._results: List[AblationResult] = []
        self._summaries: List[AblationSummary] = []
        self._lock = threading.Lock()
        
        # Import here to avoid circular imports
        from CORE.NeuralPipeline import NeuralPipeline  # scanner:ignore - circular-import guard; hoisting is contraindicated
        self._NeuralPipeline = NeuralPipeline
        
        self._ensure_output_dir()
    
    def _ensure_output_dir(self) -> None:
        os.makedirs(self.output_dir, exist_ok=True)
    
    def create_baseline_config(self, **overrides) -> Dict[str, Any]:
        """Create baseline NeuralPipeline configuration."""
        config = {
            "max_workers": 4,
            "llm_augmentation": True,
            "augmentation_apis": [
                "groq", "openrouter", "nvidia_nim", "mistral", "github", "huggingface"
            ],
        }
        config.update(overrides)
        return config
    
    def generate_ablation_configs(
        self,
        components_to_ablate: List[str],
        baseline_config: Dict[str, Any],
        train_kwargs: Dict[str, Any],
        mode: str = "single",  # "single", "pairwise", "factorial"
        repeats: int = 3,
    ) -> List[AblationConfig]:
        """
        Generate ablation experiment configurations.
        
        Args:
            components_to_ablate: List of component names to ablate
            baseline_config: Base NeuralPipeline configuration
            train_kwargs: Training arguments passed to run_end_to_end
            mode: "single" (one-at-a-time), "pairwise" (pairs), "factorial" (all combos)
            repeats: Number of repeats per configuration
        
        Returns:
            List of AblationConfig objects
        """
        configs = []
        
        # Baseline (all enabled)
        baseline_components = {c: True for c in components_to_ablate}
        configs.append(AblationConfig(
            name="baseline",
            description="All components enabled (baseline)",
            components_enabled=baseline_components,
            baseline_config=baseline_config,
            train_kwargs=train_kwargs,
            repeats=repeats,
        ))
        
        if mode == "single":
            # Single component ablation
            for comp in components_to_ablate:
                components = baseline_components.copy()
                components[comp] = False
                configs.append(AblationConfig(
                    name=f"ablate_{comp}",
                    description=f"Single ablation: {comp} disabled",
                    components_enabled=components,
                    baseline_config=baseline_config,
                    train_kwargs=train_kwargs,
                    repeats=repeats,
                ))
        
        elif mode == "pairwise":
            # Pairwise ablation (interactions)
            for i, comp1 in enumerate(components_to_ablate):
                for comp2 in components_to_ablate[i+1:]:
                    components = baseline_components.copy()
                    components[comp1] = False
                    components[comp2] = False
                    configs.append(AblationConfig(
                        name=f"ablate_{comp1}_{comp2}",
                        description=f"Pairwise ablation: {comp1} + {comp2} disabled",
                        components_enabled=components,
                        baseline_config=baseline_config,
                        train_kwargs=train_kwargs,
                        repeats=repeats,
                    ))
        
        elif mode == "factorial":
            # Full factorial (all combinations) - be careful with combinatorial explosion
            for r in range(1, len(components_to_ablate) + 1):
                for combo in itertools.combinations(components_to_ablate, r):
                    components = baseline_components.copy()
                    for comp in combo:
                        components[comp] = False
                    name = "ablate_" + "_".join(combo)
                    configs.append(AblationConfig(
                        name=name,
                        description=f"Factorial ablation: {', '.join(combo)} disabled",
                        components_enabled=components,
                        baseline_config=baseline_config,
                        train_kwargs=train_kwargs,
                        repeats=repeats,
                    ))
        
        return configs
    
    def _apply_components(self, pipeline, components_enabled: Dict[str, bool]) -> None:
        """Apply component enable/disable state to pipeline."""
        for comp_name, enabled in components_enabled.items():
            if comp_name in self.CORE_COMPONENTS:
                self.CORE_COMPONENTS[comp_name].enable_fn(pipeline, enabled)
    
    def _run_single_experiment(
        self,
        config: AblationConfig,
        run_id: int,
        source: str,
        event_type: Optional[str] = None,
    ) -> AblationResult:
        """Run a single ablation experiment."""
        start = time.perf_counter()
        
        try:
            # Create pipeline with baseline config
            pipeline = self._NeuralPipeline(**config.baseline_config)
            
            # Apply component states
            self._apply_components(pipeline, config.components_enabled)
            
            # Run end-to-end pipeline
            result = pipeline.run_end_to_end(
                source=source,
                event_type=event_type,
                **config.train_kwargs,
            )
            
            duration_ms = (time.perf_counter() - start) * 1000
            
            return AblationResult(
                config_name=config.name,
                run_id=run_id,
                components_enabled=config.components_enabled.copy(),
                metrics=result,
                duration_ms=duration_ms,
                status=result.get("status", "unknown"),
                error=result.get("error"),
            )
            
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            return AblationResult(
                config_name=config.name,
                run_id=run_id,
                components_enabled=config.components_enabled.copy(),
                metrics={},
                duration_ms=duration_ms,
                status="failed",
                error=str(e),
            )
    
    def run_ablation_config(
        self,
        config: AblationConfig,
        source: str,
        event_type: Optional[str] = None,
    ) -> List[AblationResult]:
        """Run all repeats for a single ablation configuration."""
        results = []
        
        for run_id in range(config.repeats):
            result = self._run_single_experiment(config, run_id, source, event_type)
            results.append(result)
            
            with self._lock:
                self._results.append(result)
        
        return results
    
    def run_full_ablation(
        self,
        source: str,
        components_to_ablate: Optional[List[str]] = None,
        event_type: Optional[str] = None,
        baseline_config: Optional[Dict[str, Any]] = None,
        train_kwargs: Optional[Dict[str, Any]] = None,
        mode: str = "single",
        repeats: int = 3,
    ) -> Dict[str, Any]:
        """
        Run complete ablation study.
        
        Args:
            source: Data source for training
            components_to_ablate: Components to test (default: all core components)
            event_type: Optional event type filter
            baseline_config: Base pipeline configuration
            train_kwargs: Training arguments
            mode: "single", "pairwise", or "factorial"
            repeats: Repeats per configuration
        
        Returns:
            Dict with all results and summaries
        """
        if components_to_ablate is None:
            components_to_ablate = list(self.CORE_COMPONENTS.keys())
        
        if baseline_config is None:
            baseline_config = self.create_baseline_config()
        
        if train_kwargs is None:
            train_kwargs = {
                "agent_type": "ALL",
                "limit": 200,
                "register_model": False,
            }
        
        # Generate configurations
        configs = self.generate_ablation_configs(
            components_to_ablate=components_to_ablate,
            baseline_config=baseline_config,
            train_kwargs=train_kwargs,
            mode=mode,
            repeats=repeats,
        )
        
        print(f"Running ablation study with {len(configs)} configurations, {repeats} repeats each")
        print(f"Mode: {mode}, Components: {components_to_ablate}")
        
        # Run experiments in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            for config in configs:
                for run_id in range(config.repeats):
                    future = executor.submit(
                        self._run_single_experiment,
                        config, run_id, source, event_type
                    )
                    # Carry the config itself, not just its name: below, the
                    # per-config timeout and repeat count are read back from the
                    # future's OWN config. Reading them from the loop variable
                    # `config` leaked the LAST config's values over every future,
                    # so a caller passing configs with different timeouts got one
                    # arbitrary budget applied to all of them.
                    futures.append((future, config, run_id))
            
            for future, config, run_id in futures:
                try:
                    result = future.result(timeout=config.timeout_seconds)
                    with self._lock:
                        self._results.append(result)
                    print(f"  Completed: {config.name} run {run_id+1}/{config.repeats} - {result.status}")
                except Exception as e:
                    print(f"  Failed: {config.name} run {run_id+1} - {e}")
        
        # Generate summaries
        self._generate_summaries()

        # EP455: exact Shapley marginal attribution when the measured
        # subsets cover the full factorial (post-hoc, read-only).
        try:
            attribution = self.marginal_attribution()
        except Exception:
            attribution = {"metric": "avg_reward", "shapley": {},
                           "gaps": ["attribution-error"]}
        
        # Save results
        self.save_results()
        
        return {
            "results": [asdict(r) for r in self._results],
            "summaries": [asdict(s) for s in self._summaries],
            "configs": [asdict(c) for c in configs],
            "marginal_attribution": attribution,
        }
    
    def _generate_summaries(self) -> None:
        """Generate aggregated summaries from raw results."""
        
        # Group by config_name
        by_config = defaultdict(list)
        for result in self._results:
            by_config[result.config_name].append(result)
        
        # Find baseline
        baseline_results = by_config.get("baseline", [])
        if not baseline_results:
            return
        
        # Calculate baseline averages
        baseline_metrics = {}
        for key in ["avg_reward", "episodes", "q_table_size", "duration_ms", "avg_reward_score"]:
            values = [r.metrics.get(key, 0) for r in baseline_results if r.status == "success" and key in r.metrics]
            if values:
                baseline_metrics[key] = statistics.mean(values)
        
        # Generate summary for each config
        for config_name, results in by_config.items():
            successful = [r for r in results if r.status == "success"]
            if not successful:
                continue
            
            # Average metrics
            avg_metrics = {}
            std_metrics = {}
            for key in ["avg_reward", "episodes", "q_table_size", "duration_ms", "avg_reward_score", "train_records"]:
                values = [r.metrics.get(key, 0) for r in successful if key in r.metrics]
                if values:
                    avg_metrics[key] = round(statistics.mean(values), 4)
                    std_metrics[key] = round(statistics.stdev(values) if len(values) > 1 else 0, 4)
            
            # Calculate deltas vs baseline
            metric_deltas = {}
            significance = {}
            for key, baseline_val in baseline_metrics.items():
                if key in avg_metrics:
                    delta = avg_metrics[key] - baseline_val
                    pct_change = (delta / baseline_val * 100) if baseline_val != 0 else 0
                    metric_deltas[key] = round(pct_change, 2)
                    
                    # Simple significance heuristic
                    if abs(pct_change) > 10:
                        significance[key] = "significant"
                    elif abs(pct_change) > 5:
                        significance[key] = "marginal"
                    else:
                        significance[key] = "not_significant"
            
            summary = AblationSummary(
                config_name=config_name,
                components_enabled=results[0].components_enabled,
                n_runs=len(results),
                successful_runs=len(successful),
                avg_duration_ms=round(statistics.mean([r.duration_ms for r in successful]), 2),
                avg_metrics=avg_metrics,
                std_metrics=std_metrics,
                metric_deltas=metric_deltas,
                significance=significance,
            )
            self._summaries.append(summary)
    
    def marginal_attribution(self, metric: str = "avg_reward") -> Dict[str, Any]:
        """Exact Shapley marginal-contribution audit (EP455).

        Post-hoc over the measured summaries: value(coalition) is the mean
        `metric` of the summaries whose enabled-set equals the coalition.
        Requires factorial-complete coverage (all 2^n subsets present with
        a numeric metric) and n <= 8 (exact Shapley is factorial cost).
        Anything else is a named gap, never a partial attribution.
        Read-only over summaries; the study results are untouched.
        """
        if _shapley_marginal is None:
            return {"metric": metric, "shapley": {},
                    "gaps": ["shapley-unavailable"]}
        try:
            comps: set = set()
            for s in self._summaries:
                comps |= set((s.components_enabled or {}))
            members = sorted(comps)
            if not members:
                return {"metric": metric, "shapley": {},
                        "gaps": ["no-components"]}
            if len(members) > 8:
                return {"metric": metric, "shapley": {},
                        "gaps": ["n>8-exact-intractable"]}
            by_enabled: dict = {}
            for s in self._summaries:
                en = frozenset(
                    k for k, v in (s.components_enabled or {}).items() if v)
                m = (s.avg_metrics or {}).get(metric)
                if isinstance(m, (int, float)):
                    by_enabled.setdefault(en, []).append(float(m))
            need = set()
            for r in range(len(members) + 1):
                for combo in itertools.combinations(members, r):
                    need.add(frozenset(combo))
            missing = sorted(
                [sorted(c) for c in need if c not in by_enabled])
            if missing:
                return {"metric": metric, "shapley": {},
                        "gaps": ["incomplete-factorial:%d-missing"
                                 % len(missing)],
                        "missing": missing[:8]}

            def _value(coalition: frozenset) -> float:
                vals = by_enabled.get(frozenset(coalition), [])
                return sum(vals) / len(vals) if vals else 0.0

            res = _shapley_marginal(members, _value)
            res["metric"] = metric
            res["n_coalitions"] = len(need)
            return res
        except Exception as exc:
            return {"metric": metric, "shapley": {},
                    "gaps": ["attribution-error:%s"
                             % type(exc).__name__]}

    def save_results(self, filename: Optional[str] = None) -> str:
        """Save results to JSON file."""
        if filename is None:
            filename = f"ablation_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        filepath = f"{self.output_dir}/{filename}"
        
        data = {
            "timestamp": datetime.now().isoformat(),
            "results": [asdict(r) for r in self._results],
            "summaries": [asdict(s) for s in self._summaries],
        }
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        
        return filepath
    
    def load_results(self, filepath: str) -> None:
        """Load results from JSON file."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        self._results = [AblationResult(**r) for r in data.get("results", [])]
        self._summaries = [AblationSummary(**s) for s in data.get("summaries", [])]
    
    def get_component_importance(self) -> Dict[str, Dict[str, Any]]:
        """Calculate component importance from ablation results."""
        importance = {}
        
        for summary in self._summaries:
            if summary.config_name == "baseline":
                continue
            
            # Find which components were disabled
            disabled = [k for k, v in summary.components_enabled.items() if not v]
            
            for comp in disabled:
                if comp not in importance:
                    importance[comp] = {
                        "configs_affected": 0,
                        "total_delta_pct": 0.0,
                        "significant_impacts": 0,
                        "metrics_affected": set(),
                    }
                
                importance[comp]["configs_affected"] += 1
                for metric, delta in summary.metric_deltas.items():
                    importance[comp]["total_delta_pct"] += abs(delta)
                    importance[comp]["metrics_affected"].add(metric)
                    if summary.significance.get(metric) == "significant":
                        importance[comp]["significant_impacts"] += 1
        
        # Convert sets to lists for JSON serialization
        for comp in importance:
            importance[comp]["metrics_affected"] = list(importance[comp]["metrics_affected"])
        
        return importance
    
    def print_summary(self) -> None:
        """Print human-readable summary of ablation results."""
        print("\n" + "=" * 80)
        print("ABLATION STUDY SUMMARY")
        print("=" * 80)
        
        for summary in self._summaries:
            status = "BASELINE" if summary.config_name == "baseline" else "ABLATION"
            print(f"\n{summary.config_name} [{status}]")
            print(f"  Runs: {summary.successful_runs}/{summary.n_runs} successful")
            print(f"  Avg Duration: {summary.avg_duration_ms:.1f}ms")
            print(f"  Components: {summary.components_enabled}")
            
            if summary.avg_metrics:
                print(f"  Metrics:")
                for metric, value in summary.avg_metrics.items():
                    delta = summary.metric_deltas.get(metric, 0)
                    sig = summary.significance.get(metric, "?")
                    print(f"    {metric}: {value:.4f} (Δ{delta:+.1f}%, {sig})")
        
        print("\n" + "-" * 80)
        print("COMPONENT IMPORTANCE")
        print("-" * 80)
        
        importance = self.get_component_importance()
        for comp, data in sorted(importance.items(), key=lambda x: -x[1]["total_delta_pct"]):
            print(f"\n{comp}:")
            print(f"  Total impact: {data['total_delta_pct']:.1f}%")
            print(f"  Significant impacts: {data['significant_impacts']}")
            print(f"  Metrics affected: {', '.join(data['metrics_affected'])}")


# Convenience function for quick ablation runs
def quick_ablation(
    source: str = "SystemZ",
    components: Optional[List[str]] = None,
    mode: str = "single",
    repeats: int = 3,
) -> Dict[str, Any]:
    """Quick ablation study with sensible defaults."""
    study = AblationStudy()
    return study.run_full_ablation(
        source=source,
        components_to_ablate=components,
        mode=mode,
        repeats=repeats,
    )


if __name__ == "__main__":
    # Quick test run
    study = AblationStudy()
    print("AblationStudy framework initialized")
    print(f"Available components: {list(AblationStudy.CORE_COMPONENTS.keys())}")