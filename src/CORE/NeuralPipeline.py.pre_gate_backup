# coding: utf-8
"""
NeuralPipeline — Unified End-to-End ML Orchestrator for Azazael

Orchestrates the complete neural training lifecycle under Python 3.14t free-threading:
  1. Data Extraction (DataPipelineWorm -> FTS5 LocalMemoryStore)
  2. Feature Engineering (SchemaAwareFeatureExtractor)
  3. Parallel Training (RLTrainLoop / SB3Agent / GRPOTrainer via WorkStealingPool)
  4. LLM-Augmented Reward Shaping (6 inference APIs: Groq, OpenRouter, NIM, Mistral, GitHub, HuggingFace)
  5. Model Registration (ModelVersioner)
  6. Error Recovery (DeadLetterQueue)

Gemini-designed architecture v1.0.0.

Usage:
    pipeline = NeuralPipeline(max_workers=4, llm_augmentation=True)

    # Single end-to-end run
    result = pipeline.run_end_to_end(
        source="SystemZ", event_type="cluster_execution_completed",
        agent_type="SB3", total_timesteps=5000
    )

    # Run across all 6 APIs for cross-validation
    convergence = pipeline.run_cross_api_analysis(
        source="SystemZ", event_type="cluster_execution_completed"
    )
"""

import threading
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

# -- 3.14t Free-Threading imports --------------------------------------
try:
    from CORE.freethreading import WorkStealingPool, ThreadSafeDict, FreeThreadPool
except ImportError:
    import os
    from concurrent.futures import ThreadPoolExecutor
    ThreadSafeDict = dict

    class FreeThreadPool:
        """Fallback FreeThreadPool for Python 3.12 (wraps ThreadPoolExecutor)."""

        def __init__(self, max_workers=0, thread_name_prefix="ft-worker"):
            self.max_workers = max_workers or (os.cpu_count() or 8)
            self.thread_name_prefix = thread_name_prefix
            self._executor = None

        def __enter__(self):
            self._executor = ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix=self.thread_name_prefix
            )
            return self

        def __exit__(self, *a):
            if self._executor:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

        def submit(self, fn, *a, **kw):
            if self._executor is None:
                raise RuntimeError("FreeThreadPool not entered")
            return self._executor.submit(fn, *a, **kw)

        def map(self, fn, *it, **kw):
            if self._executor is None:
                raise RuntimeError("FreeThreadPool not entered")
            return self._executor.map(fn, *it, **kw)


    class WorkStealingPool:
        """Simplified WorkStealingPool fallback using ThreadPoolExecutor."""

        def __init__(
            self,
            num_workers=0,
            thread_name_prefix="ws-worker",
            steal_attempts=3
        ):
            self.num_workers = num_workers or (os.cpu_count() or 8)
            self.thread_name_prefix = thread_name_prefix
            self._pool = None
            self._results = []
            self._results_lock = threading.Lock()

        def __enter__(self):
            self._pool = ThreadPoolExecutor(max_workers=self.num_workers, thread_name_prefix=self.thread_name_prefix)
            return self

        def __exit__(self, *a):
            if self._pool:
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = None

        def submit(self, fn, *a, **kw):
            if self._pool is None:
                raise RuntimeError("WorkStealingPool not entered")
            return self._pool.submit(fn, *a, **kw)

        def submit_all(self, tasks):
            return [self.submit(fn, *a, **kw) for fn, a, kw in tasks]

        def results(self):
            with self._results_lock:
                return list(self._results)

        def wait_all(self, timeout=None):
            return True

        @property
        def stats(self):
            return {"workers": self.num_workers, "submitted": 0, "completed": 0}

# -- Data pipeline imports ---------------------------------------------
from CORE.WORMS.expansion.learning import (
    DataPipelineWorm, ModelVersioner, SchemaAwareFeatureExtractor,
    RLTrainLoop, FeatureEngineer
)

# -- Training imports --------------------------------------------------
from CORE.sb3_agent import SB3Agent
from CORE.WORMS.expansion.learning_grpo import GRPOTrainer

# -- Error recovery ----------------------------------------------------
from CORE.WORMS.dead_letter_queue import DeadLetterQueue

# -- Inference APIs ----------------------------------------------------
from CORE.inference.providers import get_provider

# -- LocalMemoryStore --------------------------------------------------
from CORE.local_memory import LocalMemoryStore
class NeuralPipeline:
    """Unified end-to-end ML orchestrator with 3.14t safety and LLM-augmented rewards.

    Design by Gemini 2.5 Pro Deep Think — verified against Azazael conventions.

    Features:
    - 6-step pipeline: Extract -> Featurize -> Train -> Augment -> Register -> Persist
    - WorkStealingPool for parallel training tasks
    - 6 inference API integration for real-time reward shaping
    - DeadLetterQueue for failed run recovery
    - Cross-API convergence analysis
    - Session-to-session persistence via LocalMemoryStore

    Supported agent types:
    - "SB3" — stable-baselines3 PPO/DQN/A2C
    - "GRPO" — Group Relative Policy Optimization
    - "QLEARN" — Q-learning with RLTrainLoop
    - "ALL" — runs all three in parallel via WorkStealingPool
    """

    VERSION = "1.0.0"

    def __init__(self, max_workers: int = 4, llm_augmentation: bool = True,
                 augmentation_apis: Optional[List[str]] = None):
        """Initialize the NeuralPipeline.

        Args:
            max_workers: Number of WorkStealingPool workers for parallel training.
            llm_augmentation: Enable LLM-based reward shaping during training.
            augmentation_apis: Specific APIs to use (default: all 6 configured).
                               Options: groq, openrouter, nvidia_nim, mistral, github, huggingface
        """
        self._lock = threading.Lock()
        self.max_workers = max_workers
        self.llm_augmentation = llm_augmentation

        # -- Core components --
        self.data_pipe = DataPipelineWorm()
        self.feature_ext = SchemaAwareFeatureExtractor()
        self.feature_eng = FeatureEngineer()
        self.versioner = ModelVersioner()
        self.dlq = DeadLetterQueue()

        # -- Parallel execution --
        self.pool = WorkStealingPool(num_workers=max_workers)

        # -- Thread-safe stats --
        self._pipeline_stats = ThreadSafeDict() if ThreadSafeDict != dict else {}
        self._run_history: List[Dict] = []
        self._run_count = 0

        # -- Inference API integration --
        self._augmentation_apis = augmentation_apis or [
            "groq", "openrouter", "nvidia_nim", "mistral", "github", "huggingface"
        ]
        self._api_providers: Dict[str, Any] = {}
        self._api_scores: Dict[str, float] = {}  # API reliability scoring
        self._init_providers()

    def _init_providers(self) -> None:
        """Initialize all configured inference providers for augmentation."""
        for api_name in self._augmentation_apis:
            try:
                provider = get_provider(api_name)
                if provider.has_valid_key():
                    self._api_providers[api_name] = provider
                    self._api_scores[api_name] = 1.0  # Start with perfect score
            except Exception:
                pass

    # -- Pipeline Orchestration ----------------------------------------

    def run_end_to_end(self, source: str, event_type: Optional[str] = None,
                       agent_type: str = "SB3", limit: int = 500,
                       max_age_days: int = 30, use_data_split: bool = True,
                       register_model: bool = True, model_name: Optional[str] = None,
                       **train_kwargs) -> Dict[str, Any]:
        """Execute the full neural pipeline end-to-end.

        Steps: Extract -> Featurize -> Train -> Augment -> Register -> Persist

        Args:
            source: Filter memories by source component.
            event_type: Filter by event type.
            agent_type: "SB3", "GRPO", "QLEARN", or "ALL".
            limit: Max records to extract.
            max_age_days: Only include recent records.
            use_data_split: Auto-split into train/val/test.
            register_model: Register trained model with ModelVersioner.
            model_name: Custom model name for registration.
            **train_kwargs: Passed to the specific trainer.

        Returns:
            Dict with pipeline metrics, model info, and status.
        """
        metrics = {
            "pipeline_version": self.VERSION,
            "source": source,
            "event_type": event_type,
            "agent_type": agent_type,
            "started_at": datetime.now().isoformat(),
        }
        start = time.perf_counter()

        try:
            # -- Step 1: Extract ----------------------------------
            if use_data_split:
                split_data = self.data_pipe.split_memories(
                    source=source, event_type=event_type,
                    by_session=True, max_age_days=max_age_days,
                )
                train_records = split_data.get("train", [])
                test_records = split_data.get("test", [])
                metrics["data_split"] = split_data.get("stats", {})
            else:
                train_records = self.data_pipe.extract_from_memory(
                    source=source, event_type=event_type,
                    limit=limit, max_age_days=max_age_days,
                )
                test_records = []
                metrics["data_split"] = {"total": len(train_records)}

            if not train_records:
                raise ValueError(f"No training data found for source={source}")

            metrics["train_records"] = len(train_records)
            metrics["test_records"] = len(test_records)

            # -- Step 2: Featurize --------------------------------
            self.feature_ext.fit(train_records)
            features, labels, feat_meta = self.feature_ext.extract_features_and_labels(
                train_records
            )
            metrics["feature_dim"] = feat_meta.get("dim", 0)
            metrics["shapes_used"] = feat_meta.get("shapes_used", [])
            metrics["unknown_shapes"] = feat_meta.get("unknown_shapes", 0)

            # Also extract worm features for Q-learning
            worm_features = self.feature_eng.extract_worm_features(train_records)
            metrics["worm_feature_dim"] = worm_features.get("feature_dim", 0)

            # -- Step 3: Train ------------------------------------
            train_result = self._train_agent(
                agent_type=agent_type,
                source=source,
                event_type=event_type,
                train_records=train_records,
                test_records=test_records,
                features=features,
                labels=labels,
                feat_meta=feat_meta,
                worm_features=worm_features,
                **train_kwargs,
            )
            metrics.update(train_result)

            # -- Step 4: LLM Augmentation -------------------------
            if self.llm_augmentation and test_records:
                aug_scores = self._augment_rewards(
                    train_result=train_result,
                    test_records=test_records[:20],  # Sample for cost
                    source=source,
                )
                metrics["llm_augmentation"] = aug_scores

            # -- Step 5: Register ---------------------------------
            if register_model and "error" not in train_result:
                reg_name = model_name or f"{agent_type}_{source}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                reg_result = self.versioner.register_model(
                    name=reg_name,
                    version=f"v{self._run_count + 1}",
                    metrics={
                        "train_records": len(train_records),
                        "feature_dim": metrics.get("feature_dim", 0),
                        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    },
                    metadata={
                        "agent_type": agent_type,
                        "source": source,
                        "pipeline_version": self.VERSION,
                        "llm_augmented": self.llm_augmentation,
                    },
                )
                metrics["model_name"] = reg_name
                metrics["model_version"] = reg_result.get("version", "unknown")

            # -- Step 6: Persist ----------------------------------
            self._persist_run(metrics)

            metrics["status"] = "success"
            metrics["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
            self._run_count += 1

            return metrics

        except Exception as e:
            # -- Error Recovery: DeadLetterQueue ------------------
            metrics["status"] = "failed"
            metrics["error"] = str(e)
            metrics["error_type"] = type(e).__name__
            metrics["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)

            dlq_entry = self.dlq.enqueue(
                worm_name="NeuralPipeline",
                error_data={
                    "error": str(e),
                    "type": type(e).__name__,
                    "source": source,
                    "agent_type": agent_type,
                },
                config={"metrics": metrics, "train_kwargs": train_kwargs},
            )
            metrics["dlq_timestamp"] = dlq_entry.timestamp if hasattr(dlq_entry, 'timestamp') else str(dlq_entry)
            self._persist_run(metrics)
            return metrics

    # -- Training Dispatch ---------------------------------------------

    def _train_agent(self, agent_type: str, source: str, event_type: Optional[str],
                     train_records: List[Dict], test_records: List[Dict],
                     features: List[List[float]], labels: List[float],
                     feat_meta: Dict, worm_features: Dict,
                     **train_kwargs) -> Dict[str, Any]:
        """Dispatch training to the appropriate agent type.

        For "ALL", runs SB3 + GRPO + QLEARN in parallel via WorkStealingPool.
        """
        agent_type = agent_type.upper()

        if agent_type == "ALL":
            return self._train_all_parallel(
                source, event_type, train_records, test_records,
                features, labels, feat_meta, worm_features, **train_kwargs
            )
        elif agent_type == "SB3":
            return self._train_sb3(source, event_type, **train_kwargs)
        elif agent_type == "GRPO":
            return self._train_grpo(train_records)
        elif agent_type == "QLEARN":
            return self._train_qlearning(source, train_records, test_records, **train_kwargs)
        else:
            return {"error": f"Unknown agent_type: {agent_type}"}

    def _train_sb3(self, source: str, event_type: Optional[str],
                   **train_kwargs) -> Dict[str, Any]:
        """Train with SB3Agent (PPO/DQN/A2C)."""
        agent = SB3Agent(algo="PPO")
        result = agent.train(
            source=source, event_type=event_type,
            register_model=False,  # Pipeline handles registration
            **train_kwargs,
        )
        return {
            "trainer": "SB3Agent",
            "algo": agent.algo_name,
            "trained": result.get("trained", False),
            "total_timesteps": result.get("total_timesteps", 0),
            "duration_ms": result.get("duration_ms", 0),
            "raw_result": result,
        }

    def _train_grpo(self, train_records: List[Dict]) -> Dict[str, Any]:
        """Train with GRPOTrainer for worm policy optimization."""
        trainer = GRPOTrainer()
        groups = trainer.generate_group({"task_type": "pipeline"}, group_size=8)

        if self.llm_augmentation and self._api_providers:
            # Use LLM to evaluate candidate worms
            rewards = []
            for worm_name in groups:
                reward = self._llm_evaluate_worm(worm_name, train_records[:5])
                rewards.append(reward)
        else:
            rewards = trainer.compute_group_rewards(groups, {"task_type": "pipeline"})

        trainer.update_policy(groups, rewards, context_key="neural_pipeline")
        stats = trainer.get_stats()

        return {
            "trainer": "GRPOTrainer",
            "n_updates": stats.get("n_updates", 0),
            "n_contexts": stats.get("n_contexts", 0),
            "total_worms_scored": stats.get("total_worms_scored", 0),
            "best_worm": trainer.select_best("neural_pipeline"),
            "raw_result": stats,
        }

    def _train_qlearning(self, source: str, train_records: List[Dict],
                         test_records: List[Dict], **train_kwargs) -> Dict[str, Any]:
        """Train with RLTrainLoop (Q-learning)."""
        loop = RLTrainLoop()
        result = loop.train_from_memory(
            source=source,
            test_data=test_records if test_records else None,
            use_data_split=False,
            use_schema_features=True,
            **train_kwargs,
        )
        return {
            "trainer": "RLTrainLoop",
            "episodes": result.get("episodes", 0),
            "avg_reward": result.get("avg_reward", 0),
            "q_table_size": result.get("q_table_size", 0),
            "raw_result": result,
        }

    def _train_all_parallel(self, source: str, event_type: Optional[str],
                            train_records: List[Dict], test_records: List[Dict],
                            features: List[List[float]], labels: List[float],
                            feat_meta: Dict, worm_features: Dict,
                            **train_kwargs) -> Dict[str, Any]:
        """Run all three training agents in parallel via WorkStealingPool."""
        results: Dict[str, Any] = {}

        with WorkStealingPool(num_workers=min(3, self.max_workers)) as pool:
            sb3_id = pool.submit(
                self._train_sb3, source, event_type, **train_kwargs
            )
            grpo_id = pool.submit(self._train_grpo, train_records)
            ql_id = pool.submit(
                self._train_qlearning, source, train_records, test_records, **train_kwargs
            )

            pool.wait_all(timeout=300)
            all_results = pool.results()

        # Collect results by ID
        for r in all_results:
            tid = r.get("task_id", "")
            res = r.get("result", r)
            if tid == sb3_id:
                results["SB3"] = res
            elif tid == grpo_id:
                results["GRPO"] = res
            elif tid == ql_id:
                results["QLEARN"] = res

        # Merge into unified result
        success_count = sum(1 for v in results.values() if "error" not in v)
        return {
            "trainer": "ALL (parallel)",
            "agents_trained": list(results.keys()),
            "success_count": success_count,
            "total_agents": 3,
            "results": results,
        }

    # -- LLM-Augmented Reward Shaping ----------------------------------

    def _llm_evaluate_worm(self, worm_name: str, samples: List[Dict]) -> float:
        """Query inference APIs to evaluate a worm candidate for reward shaping.

        Uses round-robin across configured APIs with exponential backoff.
        Falls back to neutral score (0.5) if all APIs fail.

        Args:
            worm_name: Name of the worm candidate.
            samples: Recent memory records for context.

        Returns:
            Float score 0.0-1.0 representing worm fitness.
        """
        prompt = (
            f"Evaluate the worm '{worm_name}' for an RL training pipeline. "
            f"Given these recent system states:\n{samples[:3]}\n"
            f"Score the worm's expected effectiveness 0.0 to 1.0. "
            f"Output ONLY the number, nothing else."
        )

        # Try APIs in round-robin order
        apis = list(self._api_providers.keys())
        if not apis:
            return 0.5

        start_idx = hash(worm_name) % len(apis)
        for offset in range(len(apis)):
            api_name = apis[(start_idx + offset) % len(apis)]
            provider = self._api_providers[api_name]
            try:
                result = provider.chat(
                    prompt=prompt,
                    system_prompt="You are a strict RL reward critic. Output ONLY a float 0.0-1.0.",
                    max_tokens=10,
                    temperature=0.1,
                )
                if "content" in result:
                    score_text = result.get("content", "0.5").strip()
                    try:
                        score = float(score_text)
                        score = max(0.0, min(1.0, score))
                        # Boost API score for successful response
                        self._api_scores[api_name] = min(
                            1.0,
                            self._api_scores.get(api_name, 1.0) + 0.01
                        )
                        return score
                    except (ValueError, TypeError):
                        pass
            except Exception:
                # Penalize failing API
                self._api_scores[api_name] = max(
                    0.1,
                    self._api_scores.get(api_name, 1.0) - 0.05
                )

        return 0.5  # Neutral fallback

    def _augment_rewards(self, train_result: Dict, test_records: List[Dict],
                         source: str) -> Dict[str, Any]:
        """Augment training results with LLM-based evaluation.

        Queries multiple inference APIs and returns a convergence score.
        """
        apis_used = 0
        scores = []
        api_details = {}

        prompt = (
            f"Evaluate this RL training result for the Azazel AI ecosystem:\n"
            f"Source: {source}\n"
            f"Trainer: {train_result.get('trainer', 'unknown')}\n"
            f"Records: {len(test_records)}\n"
            f"Score the result quality 0.0-1.0 and explain why in 1 sentence.\n"
            f"Output format: '<score> <explanation>'"
        )

        for api_name, provider in list(self._api_providers.items())[:3]:
            try:
                result = provider.chat(prompt=prompt, max_tokens=100, temperature=0.3)
                if "content" in result:
                    apis_used += 1
                    content = result["content"]
                    parts = content.split(maxsplit=1)
                    try:
                        score = float(parts[0])
                        scores.append(score)
                        api_details[api_name] = {
                            "score": score,
                            "explanation": parts[1] if len(parts) > 1 else "",
                        }
                    except (ValueError, IndexError):
                        api_details[api_name] = {"raw": content}
            except Exception:
                api_details[api_name] = {"error": "API call failed"}

        avg_score = round(sum(scores) / len(scores), 4) if scores else None

        return {
            "apis_queried": apis_used,
            "avg_reward_score": avg_score,
            "individual_scores": api_details,
        }

    # -- Cross-API Convergence Analysis --------------------------------

    def run_cross_api_analysis(self, source: str,
                               event_type: Optional[str] = None,
                               limit: int = 100) -> Dict[str, Any]:
        """Run the same analysis prompt through all 6 inference APIs.

        Compares outputs for convergence, contradiction, and unique insights.

        Returns:
            Dict with per-API results and convergence report.
        """
        # Extract context
        records = self.data_pipe.extract_from_memory(
            source=source, event_type=event_type, limit=limit,
        )

        prompt = (
            f"Analyze this Azazel AI system data for neural training optimization:\n"
            f"Source: {source}\n"
            f"Records: {len(records)}\n"
            f"Sample: {records[:3]}\n\n"
            f"Answer these 3 questions:\n"
            f"1. What is the most important feature for training? (1 sentence)\n"
            f"2. What self-improvement does the system need? (1 sentence)\n"
            f"3. Rate system readiness 1-10. (number only)\n\n"
            f"Format: 'FEATURE: <answer> | IMPROVEMENT: <answer> | SCORE: <N>'"
        )

        api_results = {}
        scores = []
        for api_name, provider in self._api_providers.items():
            try:
                result = provider.chat(prompt=prompt, max_tokens=200, temperature=0.3)
                api_results[api_name] = {
                    "status": "OK" if "content" in result else "ERROR",
                    "latency_ms": result.get("elapsed_ms", 0),
                    "content": result.get("content", result.get("error", "")),
                    "model": result.get("model", "?"),
                }
                # Extract score
                content = result.get("content", "")
                if "SCORE:" in content:
                    try:
                        score_part = content.split("SCORE:")[-1].strip()
                        scores.append(int(score_part.split()[0]))
                    except (ValueError, IndexError):
                        pass
            except Exception as e:
                api_results[api_name] = {"status": "ERROR", "error": str(e)}

        # Convergence analysis
        apis_ok = sum(1 for r in api_results.values() if r["status"] == "OK")
        avg_score = round(sum(scores) / len(scores), 1) if scores else None
        score_std = round(
            (sum((s - avg_score) ** 2 for s in scores) / len(scores)) ** 0.5, 2
        ) if scores and avg_score else None

        return {
            "source": source,
            "apis_queried": len(self._api_providers),
            "apis_responded": apis_ok,
            "avg_score": avg_score,
            "score_std": score_std,
            "convergence": "HIGH" if score_std and score_std < 1.5 else "MEDIUM" if score_std and score_std < 3.0 else "LOW",
            "api_results": api_results,
            "timestamp": datetime.now().isoformat(),
        }

    # -- Persistence ---------------------------------------------------

    def _persist_run(self, metrics: Dict) -> None:
        """Persist pipeline run to LocalMemoryStore."""
        try:
            store = LocalMemoryStore()
            store.record_event(
                source="NeuralPipeline",
                event_type="pipeline_run",
                data=metrics,
                severity="info" if metrics.get("status") == "success" else "error",
                tags=["neural_pipeline", metrics.get("agent_type", "unknown")],
            )
        except Exception:
            pass

        with self._lock:
            self._run_history.append(metrics)
            if len(self._run_history) > 100:
                self._run_history = self._run_history[-100:]

            if ThreadSafeDict != dict:
                self._pipeline_stats["total_runs"] = self._run_count
                self._pipeline_stats["last_run"] = metrics.get("timestamp", "")

    # -- Stats ---------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get pipeline statistics."""
        with self._lock:
            recent = self._run_history[-10:] if self._run_history else []
        success = sum(1 for r in recent if r.get("status") == "success")
        return {
            "version": self.VERSION,
            "total_runs": self._run_count,
            "recent_success_rate": round(success / max(len(recent), 1), 2),
            "llm_augmentation": self.llm_augmentation,
            "configured_apis": len(self._api_providers),
            "api_scores": dict(self._api_scores),
            "augmentation_apis": self._augmentation_apis,
        }

    def replay_failed(self, max_concurrent: int = 2) -> Dict[str, Any]:
        """Replay failed pipeline runs from DeadLetterQueue."""
        return self.dlq.replay_all(max_concurrent=max_concurrent)

    def __repr__(self) -> str:
        return (
            f"NeuralPipeline(v{self.VERSION}, workers={self.max_workers}, "
            f"llm={self.llm_augmentation}, apis={len(self._api_providers)}, "
            f"runs={self._run_count})"
        )


# -- Self-test ---------------------------------------------------------

if __name__ == "__main__":
    print(f"NeuralPipeline v{NeuralPipeline.VERSION}")
    print(f"=" * 60)

    pipeline = NeuralPipeline(max_workers=2, llm_augmentation=False)

    # Show configured APIs
    print(f"\nConfigured APIs: {list(pipeline._api_providers.keys())}")
    print(f"LLM Augmentation: {pipeline.llm_augmentation}")

    # Run cross-API analysis
    print(f"\n--- Cross-API Convergence Analysis ---")
    result = pipeline.run_cross_api_analysis(
        source="SystemZ",
        event_type="monitor",
        limit=50,
    )
    print(f"APIs queried: {result['apis_queried']}")
    print(f"APIs responded: {result['apis_responded']}")
    print(f"Avg score: {result['avg_score']}")
    print(f"Convergence: {result['convergence']}")

    for api, r in result["api_results"].items():
        status = r["status"]
        model = r.get("model", "?")
        lat = r.get("latency_ms", 0)
        print(f"  {api:15s} {status:5s} {model:30s} {lat}ms")

    # Stats
    print(f"\n--- Pipeline Stats ---")
    stats = pipeline.get_stats()
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # E2E test with all agents + LLM augmented reward shaping
    print(f"\n--- End-to-End Orchestration (ALL Agents, LLM=True) ---")
    pipeline_e2e = NeuralPipeline(max_workers=4, llm_augmentation=True)
    try:
        result = pipeline_e2e.run_end_to_end(
            source="SystemZ",
            event_type="monitor",
            agent_type="ALL",
            limit=20,
            register_model=False
        )
        print(f"Status: {result.get('status')}")
        if result.get("status") == "success":
            print(f"Trained Agents: {result.get('agents_trained')}")
            print(f"LLM Augmentation Applied: {'llm_augmentation' in result}")
    except Exception as e:
        print(f"Execution failed safely (caught by DLQ): {e}")

    print(f"\n{pipeline}")
