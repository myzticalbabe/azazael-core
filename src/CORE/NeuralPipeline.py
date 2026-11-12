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

import os
import threading
import time
from datetime import datetime
from typing import Dict, Any, Callable, List, Optional

# -- 3.14t Free-Threading imports --------------------------------------
try:
    from CORE.freethreading import WorkStealingPool, ThreadSafeDict, FreeThreadPool
except ImportError:
    import os
    from concurrent.futures import ThreadPoolExecutor
    ThreadSafeDict = dict

    class _FreeThreadPool:
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

    FreeThreadPool = _FreeThreadPool


    class _WorkStealingPool:
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

    WorkStealingPool = _WorkStealingPool

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

# -- Audit provenance (E2, Engram #865) --------------------------------
# `graph_capsule` seals a bounded state delta and binds it to the audit
# chain's current head -- the same seam `technique_wiring/_l3_verify.py`
# uses (`at.graph_capsule(delta, at.get_head_hash() or "")`). Module scope is
# deliberate and safe: audit_trail imports only uuid7, failure_ledger and
# cryptography, and opens no session file at import (its session, atexit hook
# and anchors are all created lazily on first write), so there is neither an
# import cycle nor an import-time side effect here.
from CORE.audit_trail import (
    get_head_hash, graph_capsule, verify_capsule_chain)

# -- CUTTING_EDGE ablation (optional) ------------------------------------
# The comment that used to sit here said cutting_edge_model.py was "not yet
# committed". That was measurably false by 2026-10-15: the module is tracked
# (`git ls-files` resolves it) and imports cleanly on both interpreters. The
# import stays FUNCTION-LEVEL for a different reason -- importing the module
# executes its worm-registration decorator, so a module-scope import would
# register CuttingEdgeModel at collection time even for a pipeline that never
# opted in. tests/test_neural_pipeline_cutting_edge.py measures exactly that
# ("off means the module is never imported").
try:
    from CORE.WORMS.expansion.cutting_edge_model import (
        COMPONENTS,
        CuttingEdgeConfig,
        CuttingEdgeModel,
    )
except ImportError:                               # optional dependency
    COMPONENTS = ()
    CuttingEdgeConfig = None
    CuttingEdgeModel = None

#: The opt-in env var, and the ONLY values that count as one. Unset, empty,
#: "0" and "no" all mean off, so the backend cannot be enabled by accident and
#: an explicit "0" is never read as truthy.
_CUTTING_EDGE_ENV = "AZAZAEL_USE_CUTTING_EDGE"
_CUTTING_EDGE_OPT_IN = frozenset({"1", "true", "yes", "on"})

#: Deadline for one pooled cutting-edge step, used only when the CALLER passed no
#: timeout. The finite-difference gradient costs d_model full-batch evaluations
#: per expert, so an unbounded wait is not a safe default.
DEFAULT_TRAIN_TIMEOUT = 60.0

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
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
                 augmentation_apis: Optional[List[str]] = None,
                 use_cutting_edge: bool = False,
                 cutting_edge_config: Optional[Dict[str, Any]] = None):
        """Initialize the NeuralPipeline.

        Args:
            max_workers: Number of WorkStealingPool workers for parallel training.
            llm_augmentation: Enable LLM-based reward shaping during training.
            augmentation_apis: Specific APIs to use (default: all 6 configured).
                               Options: groq, openrouter, nvidia_nim, mistral, github, huggingface
            use_cutting_edge: Opt IN to the CuttingEdgeModel backend (default
                              off). Off is the pre-existing behaviour: "SB3"
                              stays the default agent and "ALL" keeps its three.
            cutting_edge_config: Overrides for CuttingEdgeConfig, e.g.
                              {"n_experts": 12, "use_attention": False}. Held on
                              the pipeline so a switch can be flipped before the
                              model is ever built.
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
        # EP446: previous run capsule's signature. Each run mints its
        # capsule chained to this (prev_hash), so the run history forms a
        # verifiable chain instead of isolated capsules.
        self._last_capsule_sig: str = ""

        # -- Inference API integration --
        self._augmentation_apis = augmentation_apis or [
            "groq", "openrouter", "nvidia_nim", "mistral", "github", "huggingface"
        ]
        self._api_providers: Dict[str, Any] = {}
        self._api_scores: Dict[str, float] = {}  # API reliability scoring
        self._init_providers()

        # -- Cutting-edge backend (opt-in; off by default) ------------------
        # The model is deliberately NOT built here. Building it imports the
        # module (registering a worm as a side effect of construction) and would
        # hand back a model a caller never asked for. `cutting_edge_switches` is
        # readable in advance of any of that.
        self.use_cutting_edge = bool(use_cutting_edge)
        # A plain mapping of overrides is the common case, but a caller may hand
        # in a ready CuttingEdgeConfig (tests/test_cutting_edge_production_fixes
        # does). Keeping the OBJECT matters: re-deriving it from `to_dict()` would
        # re-validate a config the caller already built.
        if isinstance(cutting_edge_config, dict):
            self._cutting_edge_config: Dict[str, Any] = dict(cutting_edge_config)
            self._cutting_edge_config_obj: Optional[Any] = None
        else:
            self._cutting_edge_config = {}
            self._cutting_edge_config_obj = cutting_edge_config
        self._cutting_edge_model: Optional[Any] = None

    def _init_providers(self) -> None:
        """Initialize all configured inference providers for augmentation."""
        for api_name in self._augmentation_apis:
            try:
                provider = get_provider(api_name)
                if provider.has_valid_key():
                    self._api_providers[api_name] = provider
                    self._api_scores[api_name] = 1.0  # Start with perfect score
            except Exception as exc:
                note_failure("NeuralPipeline:L202", exc)

    # -- Cutting-edge backend (opt-in) ---------------------------------

    @staticmethod
    def opt_in_from_env(env: Optional[Dict[str, str]] = None) -> bool:
        """Whether AZAZAEL_USE_CUTTING_EDGE asks for the cutting-edge backend.

        The mapping is a PARAMETER rather than a direct `os.environ` read, so the
        decision is testable without mutating process state and this helper reads
        no global of its own. Only an exact opt-in is true: an empty string, "0"
        and "no" all mean off.
        """
        source = os.environ if env is None else env
        return str(source.get(_CUTTING_EDGE_ENV, "")).strip().lower() \
            in _CUTTING_EDGE_OPT_IN

    @property
    def cutting_edge_switches(self) -> Dict[str, bool]:
        """The `use_*` switches configured for this pipeline, pre-build.

        Readable without building the model, which is what lets a caller inspect
        a pipeline before anything is registered or allocated.
        """
        if self._cutting_edge_config_obj is not None:
            return {k: bool(v) for k, v in
                    self._cutting_edge_config_obj.ablation_switches().items()}
        return {k: bool(v) for k, v in self._cutting_edge_config.items()
                if k.startswith("use_")}

    @property
    def cutting_edge_model(self) -> Any:
        """The cutting-edge model, built on FIRST USE -- never at construction."""
        if self._cutting_edge_model is None:
            if CuttingEdgeModel is None or CuttingEdgeConfig is None:
                raise RuntimeError(
                    "CuttingEdgeModel is not importable here, so the cutting-edge "
                    "backend cannot be built: fix CORE.WORMS.expansion."
                    "cutting_edge_model, or leave AZAZAEL_USE_CUTTING_EDGE unset")
            cfg = self._cutting_edge_config_obj
            if cfg is None:
                cfg = CuttingEdgeConfig(**self._cutting_edge_config)
            self._cutting_edge_model = CuttingEdgeModel(cfg)
        return self._cutting_edge_model

    def ablate_cutting_edge(self, component: str) -> Dict[str, Any]:
        """Switch `component` off, and report what the model now is.

        The switch is recorded on the PIPELINE rather than edited into the live
        model, and the built model is dropped so the next access rebuilds the
        ablated configuration. That matches how CuttingEdgeModel itself is
        addressed (`ablate()` returns a NEW model): an in-place edit is how a
        sweep ends up comparing a state it already mutated.
        """
        if component not in COMPONENTS:
            raise KeyError(
                "unknown component %r (expected one of %r)" % (component, COMPONENTS))
        if self._cutting_edge_config_obj is not None:
            self._cutting_edge_config_obj = self._cutting_edge_config_obj.variant(
                **{"use_" + component: False})
        else:
            self._cutting_edge_config["use_" + component] = False
        self._cutting_edge_model = None
        model = self.cutting_edge_model
        return {
            "component": component,
            "active_components": list(model.active_components()),
            "switches": dict(model.config.ablation_switches()),
            "config_fingerprint": model.config_fingerprint(),
        }

    # -- Pipeline Orchestration ----------------------------------------

    def run_end_to_end(self, source: str, event_type: Optional[str] = None,
                       agent_type: Optional[str] = None, limit: int = 500,
                       max_age_days: int = 30, use_data_split: bool = True,
                       register_model: bool = True, model_name: Optional[str] = None,
                       records: list[dict] | None = None,  # frozen-record seam (from #850)
                       **train_kwargs) -> Dict[str, Any]:
        """Execute the full neural pipeline end-to-end.

        Steps: Extract -> Featurize -> Train -> Augment -> Register -> Persist

        Args:
            source: Filter memories by source component.
            event_type: Filter by event type.
            agent_type: "SB3", "GRPO", "QLEARN", "CUTTING_EDGE", or "ALL".
                        None (the default) resolves to "CUTTING_EDGE" when the
                        pipeline opted in and "SB3" otherwise -- the flag chooses
                        a DEFAULT and never overrides an explicit agent_type.
            limit: Max records to extract.
            max_age_days: Only include recent records.
            use_data_split: Auto-split into train/val/test.
            register_model: Register trained model with ModelVersioner.
            model_name: Custom model name for registration.
            **train_kwargs: Passed to the specific trainer.

        Returns:
            Dict with pipeline metrics, model info, and status.
        """
        # The opt-in chooses the DEFAULT agent; an explicit caller value always
        # wins, so enabling the flag cannot change an existing call site.
        if agent_type is None:
            agent_type = "CUTTING_EDGE" if self.use_cutting_edge else "SB3"

        metrics = {
            "pipeline_version": self.VERSION,
            "source": source,
            "event_type": event_type,
            "agent_type": agent_type,
            "started_at": datetime.now().isoformat(),
        }
        start = time.perf_counter()

        try:
            # -- Step 1: Extract (or use frozen records from #850) ----------
            if records is not None:
                # Frozen-record seam: use the provided records and skip store extraction
                # this is the explicit seam that lets technique-intake records reach
                # the pipeline deterministically without touching the live store.
                # Note: records= already leaks through **train_kwargs accidentally;
                # this explicit path is the intended seam and kwargs behavior is unchanged.
                train_records = records
                test_records = []
                metrics["data_split"] = {"total": len(train_records), "note": "frozen-seam"}
            elif use_data_split:
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
                register_model=register_model,
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
            # The cutting-edge path registers through the MODEL's own adapter,
            # which is what carries the config fingerprint -- the one thing that
            # keeps two ablations from sharing a version. Registering it again
            # here would file a second, timestamped version of the same model.
            if (register_model and "error" not in train_result
                    and not train_result.get("registered_by_model")):
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
            # -- Error Recovery: DeadLetterQueue ------------------
            note_failure("NeuralPipeline:L330", e)
            metrics["status"] = "failed"
            metrics["error"] = str(e)
            metrics["error_type"] = type(e).__name__
            metrics["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)

            dlq_entry = self.dlq.enqueue(
                worm_name="NeuralPipeline",
                error={
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
                     register_model: bool = False,
                     **train_kwargs) -> Dict[str, Any]:
        """Dispatch training to the appropriate agent type.

        For "ALL", runs SB3 + GRPO + QLEARN in parallel via WorkStealingPool, plus
        CUTTING_EDGE when the pipeline opted in.

        `register_model` is an EXPLICIT parameter rather than a key in
        `train_kwargs`: the three legacy trainers are handed `**train_kwargs`, and
        `_train_sb3` already passes `register_model=False` to its own agent, so
        routing it through the shared dict would collide as a duplicate kwarg.
        """
        agent_type = agent_type.upper()

        if agent_type == "ALL":
            return self._train_all_parallel(
                source, event_type, train_records, test_records,
                features, labels, feat_meta, worm_features,
                register_model=register_model, **train_kwargs
            )
        elif agent_type == "SB3":
            return self._train_sb3(source, event_type, train_records=train_records, **train_kwargs)
        elif agent_type == "GRPO":
            return self._train_grpo(train_records)
        elif agent_type == "QLEARN":
            return self._train_qlearning(source, train_records, test_records, **train_kwargs)
        elif agent_type == "CUTTING_EDGE":
            return self._train_cutting_edge(
                source, event_type, train_records, test_records,
                features, labels, feat_meta, worm_features,
                register_model=register_model, **train_kwargs
            )
        else:
            return {"error": f"Unknown agent_type: {agent_type}"}

    def _train_sb3(self, source: str, event_type: Optional[str], train_records: Optional[List[Dict]] = None, **train_kwargs) -> Dict[str, Any]:
        """Train with SB3Agent (PPO/DQN/A2C)."""
        worm_id = train_kwargs.pop("worm_id", None)
        agent = SB3Agent(algo="PPO", worm_id=worm_id)
        result = agent.train(
            source=source, event_type=event_type,
            records=train_records,  # Frozen-record seam from #850
            register_model=False,  # Pipeline handles registration
            **train_kwargs,
        )
        return {
            "trainer": "SB3Agent",
            "algo": agent.algo_name,
            "worm_id": agent.worm_id,
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
        # Strip SB3-only kwargs (total_timesteps) that aren't part of
        # RLTrainLoop.train_from_memory's signature — the shared ALL path
        # passes the same train_kwargs to every agent.
        qlearn_kwargs = {
            k: v for k, v in train_kwargs.items()
            if k not in ("total_timesteps",)
        }
        loop = RLTrainLoop()
        result = loop.train_from_memory(
            source=source,
            test_data=test_records if test_records else None,
            use_data_split=False,
            use_schema_features=True,
            **qlearn_kwargs,
        )
        return {
            "trainer": "RLTrainLoop",
            "episodes": result.get("episodes", 0),
            "avg_reward": result.get("avg_reward", 0),
            "q_table_size": result.get("q_table_size", 0),
            "raw_result": result,
        }

    def _train_cutting_edge(self, source: str, event_type: Optional[str],
                            train_records: List[Dict], test_records: List[Dict],
                            features: List[List[float]], labels: List[float],
                            feat_meta: Dict, worm_features: Dict,
                            register_model: bool = False,
                            timeout: Optional[float] = None,
                            **train_kwargs) -> Dict[str, Any]:
        """One CuttingEdgeModel step, driven entirely through the model's adapters.

        Every boundary is the REAL adapter -- from_schema_features, reward,
        train_parallel, record_failure, register_version -- so a run measures the
        model rather than a pipeline-side reimplementation of it. Importing the
        class and never calling it is what made the backend reachable only from
        tests.

        Two orderings here are deliberate, and each is pinned by a test:

        * the `routing` verdict is sampled from the forward that ran BEFORE
          `train_parallel` nudges the gate, so it is the level the run actually
          saw. `routing_after_balance` then carries the post-remediation level
          derived from the loop's own full-batch snapshot, because a verdict with
          no second number cannot tell "the remediation did not reach the batch"
          apart from "nothing measured the batch afterwards";
        * the reward is computed AFTER the step so its load term is the
          post-remediation `used_fraction` -- the state the run ended in, not the
          state it was fitted on -- falling back to the pre-loop value when the
          loop reported nothing.

        `trained` is REPORTED, not assumed: it is true only when at least one
        expert updated AND the objective actually improved, because
        `train_parallel` returns None losses for a step that updated nothing. A
        configuration with no MoE has no experts to update, so it says so rather
        than claiming arithmetic that never ran.
        """
        model = self.cutting_edge_model
        try:
            if not features:
                raise ValueError(
                    "the cutting-edge backend needs at least one feature row")

            # -- adapter 1: the feature boundary -----------------------------
            adapted = model.from_schema_features(features, meta=feat_meta)
            result: Dict[str, Any] = {
                "trainer": "CuttingEdgeModel",
                "feature_meta": dict(adapted.get("feature_meta") or {}),
                "forward_shape": list(adapted.get("shape") or ()),
                "active_components": list(adapted.get("active_components")
                                          or model.active_components()),
                "ablation_switches": dict(model.config.ablation_switches()),
                "config_fingerprint": model.config_fingerprint(),
            }
            routing = self._cutting_edge_routing(model)
            if routing is not None:
                result["routing"] = routing

            # -- adapter 2: the pooled training step -------------------------
            # `timeout` is the CALLER's, forwarded rather than defaulted: a
            # caller asking for a deadline must get one, and a hard-coded value
            # here is how a timeout test passes while measuring nothing.
            with WorkStealingPool(num_workers=max(1, self.max_workers)) as pool:
                step = model.train_parallel(
                    pool, tokens=adapted.get("output") or features,
                    timeout=DEFAULT_TRAIN_TIMEOUT if timeout is None else timeout)
            result["train_step"] = {
                "group_id": step.get("group_id"),
                "waited_ok": bool(step.get("waited_ok")),
                "errors": list(step.get("errors") or []),
            }
            result["experts_updated"] = int(step.get("experts_updated", 0))
            result["loss_before"] = step.get("loss_before")
            result["loss_after"] = step.get("loss_after")
            # A delta whose before-half is a sum over an EMPTY set is a number
            # about nothing, so `train_parallel` reports None there and it is
            # carried through as None rather than dropped.
            result["loss_delta"] = step.get("loss_delta")

            balancing = step.get("balancing")
            if balancing:
                result["balancing"] = balancing
            routing_after = self._cutting_edge_routing_after(balancing, routing)
            if routing_after is not None:
                result["routing_after_balance"] = routing_after

            # -- adapter 3: reward shaping (local, and after the step) -------
            result["reward"] = model.reward(
                self._cutting_edge_signals(features, labels, result,
                                           routing, routing_after),
                provider_names=list(self._api_providers.keys()),
                allow_network=False,
            )

            if step.get("skipped"):
                result["trained"] = False
                result["trained_reason"] = str(
                    step.get("reason") or "no experts to update in this config")
            else:
                improved = (result["loss_before"] is not None
                            and result["loss_after"] is not None
                            and result["loss_after"] < result["loss_before"])
                result["trained"] = bool(result["experts_updated"] > 0 and improved)
                if not result["trained"]:
                    # Names the pool and its wait verdict, so a timeout is
                    # distinguishable from a quiet no-op.
                    result["trained_reason"] = (
                        "the pool step updated no expert (waited_ok=%r, "
                        "group_id=%r, experts_updated=%d, loss_before=%r, "
                        "loss_after=%r)" % (
                            result["train_step"]["waited_ok"],
                            result["train_step"]["group_id"],
                            result["experts_updated"],
                            result["loss_before"], result["loss_after"]))
                elif register_model:
                    # -- adapter 4: registration, carrying the fingerprint ----
                    reg = model.register_version(
                        self.versioner,
                        {"train_records": len(train_records),
                         "experts_updated": result["experts_updated"]},
                        metadata={
                            "source": source,
                            "pipeline_version": self.VERSION,
                            "active_components": list(model.active_components()),
                            "config_fingerprint": model.config_fingerprint(),
                            "ablation_switches": dict(model.config.ablation_switches()),
                        })
                    result["model_name"] = "cutting_edge_model"
                    result["model_version"] = reg.get("version", "unknown")
                    result["version_registered"] = True
                    # Tells run_end_to_end's step 5 not to file a second version.
                    result["registered_by_model"] = True
            if register_model and not result.get("registered_by_model"):
                # Asked to register and could not -- either the step updated
                # nothing or there were no experts to update. Say so, rather than
                # leaving the caller to read an absence as an approval.
                result["registration_skipped"] = (
                    "no version was filed under cutting_edge_model: %s"
                    % result.get("trained_reason", "the model did not train"))
            return result
        except Exception as exc:
            return self._cutting_edge_failure(model, source, exc)

    @staticmethod
    def _cutting_edge_routing(model: Any) -> Optional[Dict[str, Any]]:
        """Summarise the gate load of the forward that has already run.

        Measured from the MoE's own routing on the rows the forward actually fed
        it (`last_inputs`), so this is the same batch the load-balancing loop
        snapshots -- which is what makes the run's verdict comparable to the
        loop's `before_full` instead of a second, unrelated number.
        """
        moes = getattr(model, "moes", None) or []
        if not moes:
            return None
        moe = moes[0]
        rows = getattr(moe, "last_inputs", None) or []
        chosen, _weights, _probs = moe.route(rows)
        counts = [0] * moe.n_experts
        for top in chosen:
            for expert_id in top:
                counts[expert_id] += 1
        used = sum(1 for c in counts if c > 0)
        return {
            "counts": counts,
            "used_experts": used,
            "n_experts": moe.n_experts,
            "used_fraction": (used / float(moe.n_experts)) if moe.n_experts else 0.0,
        }

    @classmethod
    def _cutting_edge_routing_after(cls, balancing: Optional[Dict[str, Any]],
                                    routing: Optional[Dict[str, Any]]
                                    ) -> Optional[Dict[str, Any]]:
        """The POST-remediation load, derived from the loop's own full-batch number."""
        if not balancing or routing is None:
            return None
        after_full = balancing.get("after_full")
        if not after_full:
            return None
        counts = list(after_full.get("counts") or [])
        used = sum(1 for c in counts if c > 0)
        n_experts = routing["n_experts"]
        return {
            "counts": counts,
            "used_experts": used,
            "n_experts": n_experts,
            "used_fraction": (used / float(n_experts)) if n_experts else 0.0,
        }

    @staticmethod
    def _cutting_edge_signals(features: List[List[float]], labels: List[float],
                              result: Dict[str, Any],
                              routing: Optional[Dict[str, Any]],
                              routing_after: Optional[Dict[str, Any]]
                              ) -> List[float]:
        """The three real, bounded signals the reward adapter is handed.

        `[progress, coverage, load]`, all clamped to 0..1 because the adapter's
        local rubric is a mean. Every term is derived from the run -- an
        improvement ratio, a row-fill fraction, and the LOAD the run ended on --
        so a fabricated constant would be a reward for nothing.

        The load term prefers the post-remediation `used_fraction` and falls back
        to the pre-loop one, so the reward sees the state the run produced rather
        than the state it was fitted on. A step that updated nothing reports None
        losses, which reads as zero progress instead of raising: a reward path
        that cannot describe a no-op would turn a timeout into a crash.
        """
        rows = [r for r in features if r]
        coverage = 0.0
        if rows:
            filled = sum(1 for r in rows if any(abs(float(v)) > 0.0 for v in r))
            coverage = filled / len(rows)

        before = result.get("loss_before")
        after = result.get("loss_after")
        progress = 0.0
        if before is not None and after is not None and float(before) > 0.0:
            progress = (float(before) - float(after)) / float(before)

        source = routing_after or routing
        load = float((source or {}).get("used_fraction", 0.0) or 0.0)
        return [max(0.0, min(1.0, progress)),
                max(0.0, min(1.0, coverage)),
                max(0.0, min(1.0, load))]

    def _cutting_edge_failure(self, model: Any, source: str,
                              exc: BaseException) -> Dict[str, Any]:
        """Route a failed step to the DLQ through the model, then report it.

        The letter is enqueued BEFORE the dict is returned, so a caller that only
        reads the result still finds the failure waiting for replay. `trained` is
        absent rather than False: the run must not claim a step it did not take.
        """
        note_failure("NeuralPipeline:_train_cutting_edge", exc)
        error = {"error": str(exc), "type": type(exc).__name__, "source": source}
        try:
            model.record_failure(self.dlq, error, worm_name="CuttingEdgeModel")
        except Exception as dlq_exc:                   # a broken DLQ must not mask
            note_failure("NeuralPipeline:_cutting_edge_failure", dlq_exc)
            error["dlq_error"] = str(dlq_exc)
        return {
            "trainer": "CuttingEdgeModel",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }

    def _train_all_parallel(self, source: str, event_type: Optional[str],
                            train_records: List[Dict], test_records: List[Dict],
                            features: List[List[float]], labels: List[float],
                            feat_meta: Dict, worm_features: Dict,
                            register_model: bool = False,
                            **train_kwargs) -> Dict[str, Any]:
        """Run the training agents in parallel via WorkStealingPool.

        "ALL" keeps its pre-existing three agents unless the pipeline opted in:
        the fourth is added by the flag, not by default, so no existing caller's
        `total_agents` changes.
        """
        results: Dict[str, Any] = {}
        calls: List[tuple] = [
            ("SB3", (self._train_sb3, source, event_type), dict(train_kwargs, train_records=train_records)),
            ("GRPO", (self._train_grpo, train_records), {}),
            ("QLEARN", (self._train_qlearning, source, train_records, test_records),
             dict(train_kwargs)),
        ]
        if self.use_cutting_edge:
            calls.append((
                "CUTTING_EDGE",
                (self._train_cutting_edge, source, event_type, train_records,
                 test_records, features, labels, feat_meta, worm_features),
                dict(train_kwargs, register_model=register_model),
            ))

        with WorkStealingPool(num_workers=min(len(calls), self.max_workers)) as pool:
            task_ids: Dict[str, Any] = {}
            for name, args, kwargs in calls:
                task_ids[name] = pool.submit(*args, **kwargs)

            pool.wait_all(timeout=300)
            all_results = pool.results()

        # Collect results by ID
        for r in all_results:
            tid = r.get("task_id", "")
            res = r.get("result", r)
            for name, task_id in task_ids.items():
                if tid == task_id:
                    results[name] = res

        # Merge into unified result
        success_count = sum(
            1 for v in results.values()
            if v is not None and "error" not in v
        )
        return {
            "trainer": "ALL (parallel)",
            "agents_trained": list(results),
            "success_count": success_count,
            "total_agents": len(calls),
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
                    except (ValueError, TypeError) as exc:
                        note_failure("NeuralPipeline:L541", exc)
            except Exception as exc:
                # Penalize failing API
                # Penalize failing API
                note_failure("NeuralPipeline:L543", exc)
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
                    except (ValueError, IndexError) as exc:
                        note_failure("NeuralPipeline:L585", exc)
                        api_details[api_name] = {"raw": content}
            except Exception as exc:
                note_failure("NeuralPipeline:L587", exc)
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
                    except (ValueError, IndexError) as exc:
                        note_failure("NeuralPipeline:L644", exc)
            except Exception as e:
                note_failure("NeuralPipeline:L646", e)
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
        """Persist pipeline run to LocalMemoryStore.

        E2 (Engram #865): the persisted payload carries a hash-chained
        provenance capsule -- `graph_capsule` over a bounded run summary,
        bound to the audit chain's current head. Additive only (one new
        `capsule` key; every existing key is written exactly as before), and
        FAIL-OPEN: provenance is a side effect of persisting a run, never a
        precondition for it, so a signing problem is recorded and the run
        still persists.
        """
        # Bounded on purpose: identity + status, not the whole metrics dict
        # (that grows with training results, and the signed payload is what
        # every capsule has to carry).
        try:
            # EP446: chain to the previous run's capsule signature (audit
            # head hash only for the first run). A benign race here (two
            # concurrent persists reading the same prev) fails the link
            # check loudly below instead of corrupting silently.
            with self._lock:
                prev_sig = self._last_capsule_sig
            metrics["capsule"] = graph_capsule(
                {
                    "pipeline": "NeuralPipeline",
                    "version": self.VERSION,
                    "agent_type": str(metrics.get("agent_type", "")),
                    "source": str(metrics.get("source", "")),
                    "status": str(metrics.get("status", "")),
                    "run_index": self._run_count,
                    "timestamp": str(metrics.get("timestamp", "")),
                },
                prev_sig or get_head_hash() or "",
            )
        except Exception as exc:
            note_failure("NeuralPipeline:provenance-capsule", exc)
        try:
            store = LocalMemoryStore()
            store.record_event(
                source="NeuralPipeline",
                event_type="pipeline_run",
                data=metrics,
                severity="info" if metrics.get("status") == "success" else "error",
                tags=["neural_pipeline", metrics.get("agent_type", "unknown")],
            )
        except Exception as exc:
            note_failure("NeuralPipeline:L680", exc)

        with self._lock:
            self._run_history.append(metrics)
            if len(self._run_history) > 100:
                self._run_history = self._run_history[-100:]
            # EP446: verify the pipeline's own capsule chain. The bounded
            # run history carries one capsule per run, each chained to its
            # predecessor, so verify_capsule_chain re-verifies every seal
            # and the signature linkage. The verdict lands on the metrics
            # (visible to the caller) and in stats; a broken chain is
            # reported, never fatal. (The stored payload was recorded above
            # before the chain verdict existed; the verdict rides the live
            # metrics and stats instead of rewriting history.)
            try:
                chain = [m.get("capsule")
                         for m in self._run_history[-25:]
                         if isinstance(m.get("capsule"), dict)]
                verdict = verify_capsule_chain(chain)
            except Exception as exc:
                note_failure("NeuralPipeline:capsule-chain", exc)
                verdict = {"n": 0, "chain_ok": False, "carried": False,
                           "reason": "verify-error"}
            metrics["capsule_chain"] = {
                "n": verdict.get("n"),
                "chain_ok": verdict.get("chain_ok"),
                "carried": verdict.get("carried"),
                "reason": verdict.get("reason", "")}
            cap = metrics.get("capsule") or {}
            if isinstance(cap, dict) and cap.get("signature"):
                self._last_capsule_sig = str(cap["signature"])
            if ThreadSafeDict != dict:
                self._pipeline_stats["capsule_chain_ok"] = bool(
                    verdict.get("chain_ok"))

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

    def replay_failed(
        self,
        execute_fn: Optional[Callable[[str, dict], dict]] = None,
        max_concurrent: int = 2,
    ) -> Dict[str, Any]:
        """Replay pending dead letters through `execute_fn`.

        Contract pinned by tests/test_replay_failed.py:

          * an EMPTY queue needs no executor -- it returns
            ``{"replayed": 0, "results": []}``;
          * pending letters with NO executor raise RuntimeError naming both the
            missing argument and the queue depth. It must not surface as a
            TypeError: the previous signature did not accept `execute_fn` at
            all, so a caller that had one failed with "got an unexpected keyword
            argument 'execute_fn'" and a caller that had none got the DLQ's
            "missing 1 required positional argument: 'execute_fn'" -- neither of
            which says what to pass;
          * pending letters WITH an executor delegate to
            ``DeadLetterQueue.replay_all(execute_fn, max_concurrent=...)`` (the
            real signature takes the executor positionally) and report the
            count, so a caller can tell how many actually ran.
        """
        pending = self.dlq.get_pending() if hasattr(self.dlq, "get_pending") else []
        if not pending:
            return {"replayed": 0, "results": []}
        if execute_fn is None:
            raise RuntimeError(
                f"replay_failed() has {len(pending)} dead letter(s) to replay "
                f"but no executor: pass execute_fn=<callable(worm_name, config)>."
            )
        results = self.dlq.replay_all(execute_fn, max_concurrent=max_concurrent)
        return {"replayed": len(results), "results": results}

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
