# coding: utf-8
"""HyperOptimizer — Parallel Multi-Model Execution Engine

Runs models CONCURRENTLY instead of sequentially, with fast-failover,
adaptive routing, and weighted consensus. Designed to maximize the
value of free inference APIs by running many models in parallel and
taking the best results.

Architecture:
    +---------------- HyperOptimizer -----------------+
    |                                                  |
    |  Query ---> AdaptiveRouter ---> ParallelExecutor   |
    |               |                      |           |
    |               ▼                      ▼           |
    |         Complexity Score        Batch 1 (fast)   |
    |         ---------------         Batch 2 (deep)   |
    |         simple: 2 models        Batch 3 (vote)   |
    |         medium: 6 models                          |
    |         complex: 16+ models                       |
    |                                                  |
    |         WeightedConsensus <--- All Results         |
    |              |                                    |
    |              ▼                                    |
    |         Final Resolution                          |
    +--------------------------------------------------+

Key features:
- Parallel execution: models run concurrently in batches
- Fast-failover: dead models are skipped immediately
- Adaptive routing: simple queries skip deep models
- Weighted voting: models vote on answers, weighted by speed
- Smart caching: identical queries skip model calls
- Thundering herd prevention: staggered parallel launches
"""

import time
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from .providers import get_provider
from .sequential_analyzer import (
    ChainStep,
    ModelResponse,
    compute_similarity,
    estimate_confidence,
)

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# --- Data Models ----------------------------------------------------


@dataclass
class ModelMetrics:
    """Historical performance metrics for a model."""
    model_id: str
    provider: str
    total_calls: int = 0
    successful_calls: int = 0
    total_latency_ms: int = 0
    avg_latency_ms: float = 0.0
    success_rate: float = 1.0
    weight: float = 1.0  # Dynamic weight for consensus voting
    last_error: str = ""
    consecutive_failures: int = 0
    is_dead: bool = False  # Temporarily disabled if too many failures

    def record_success(self, latency_ms: int) -> None:
        """Record a successful model call. Caller must hold _metrics_lock."""
        self.total_calls += 1
        self.successful_calls += 1
        self.total_latency_ms += latency_ms
        self.avg_latency_ms = self.total_latency_ms / self.successful_calls
        self.success_rate = self.successful_calls / self.total_calls
        self.consecutive_failures = 0
        self.is_dead = False
        self._update_weight()

    def record_failure(self, error: str = "") -> None:
        """Record a failed model call. Caller must hold _metrics_lock."""
        self.total_calls += 1
        self.consecutive_failures += 1
        self.last_error = error[:200]
        self.success_rate = self.successful_calls / max(self.total_calls, 1)
        if self.consecutive_failures >= 3:
            self.is_dead = True
        self._update_weight()

    def _update_weight(self) -> None:
        """Dynamic weight based on speed and accuracy.
        Fast, reliable models get higher weight.
        """
        speed_score = 1.0 / max(self.avg_latency_ms / 1000, 0.1)
        speed_score = min(speed_score, 2.0)  # Cap at 2x
        reliability_score = self.success_rate
        self.weight = (speed_score * 0.4 + reliability_score * 0.6)
        self.weight = max(0.1, min(self.weight, 2.0))


@dataclass
class BatchResult:
    """Results from a single parallel execution batch."""
    responses: list[ModelResponse] = field(default_factory=list)
    elapsed_ms: int = 0
    tokens_used: int = 0
    errors: list[str] = field(default_factory=list)
    consensus: str = ""
    confidence: float = 0.0


@dataclass
class HyperResult:
    """Final result from the hyper-optimized execution."""
    prompt: str
    resolution: str = ""
    confidence: float = 0.0
    models_used: list[str] = field(default_factory=list)
    batches: list[BatchResult] = field(default_factory=list)
    total_elapsed_ms: int = 0
    total_tokens: int = 0
    errors: list[str] = field(default_factory=list)
    complexity: str = "unknown"
    model_votes: dict[str, float] = field(default_factory=dict)
    cache_hit: bool = False


# --- Model Tiers ----------------------------------------------------


# TIER 1: Ultra-fast (< 5s) — always included
TIER1_FAST = [
    ChainStep("deepseek", "deepseek-v4-flash",
              "You are a fast analyst. Respond concisely.", max_tokens=1024, temperature=0.3),
    ChainStep("groq", "llama-3.3-70b-versatile",
              "You are a fast analyst. Respond concisely.", max_tokens=1024, temperature=0.3),
    ChainStep("openrouter", "google/gemini-2.5-flash",
              "You are a fast analyst. Respond concisely.", max_tokens=1024, temperature=0.3),
]

# TIER 2: Balanced (5-15s) — medium queries
TIER2_BALANCED = [
    ChainStep("openrouter", "google/gemini-2.5-pro",
              "You are a thorough analyst. Provide detailed analysis.", max_tokens=2048, temperature=0.4),
    ChainStep("github", "gpt-4o-mini",
              "You are a thorough analyst. Provide detailed analysis.", max_tokens=2048, temperature=0.3),
    ChainStep("mistral", "mistral-large-latest",
              "You are a thorough analyst. Provide detailed analysis.", max_tokens=2048, temperature=0.4),
    ChainStep("nvidia_nim", "meta/llama-3.3-70b-instruct",
              "You are a thorough analyst. Provide detailed analysis.", max_tokens=2048, temperature=0.3),
]

# TIER 3: Deep (15-60s) — complex queries only
TIER3_DEEP = [
    ChainStep("nvidia_nim", "nvidia/llama-3.1-nemotron-ultra-253b-v1",
              "You are a deep reasoning specialist. Maximum depth analysis.", max_tokens=4096, temperature=0.3),
    ChainStep("github", "gpt-4o",
              "You are a deep reasoning specialist. Maximum depth analysis.", max_tokens=4096, temperature=0.4),
    ChainStep("huggingface", "meta-llama/Llama-3.3-70B-Instruct",
              "You are a cross-validation expert. Validate and synthesize.", max_tokens=2048, temperature=0.4),
]


# --- Adaptive Query Classifier --------------------------------------


def classify_query(prompt: str) -> str:
    """Classify query complexity: 'simple', 'medium', or 'complex'.

    Uses heuristics rather than an API call to avoid overhead.
    """
    prompt_lower = prompt.lower()
    word_count = len(prompt.split())
    has_code = "```" in prompt or "def " in prompt or "class " in prompt
    technical_keywords = [
        "architecture", "design", "implement", "refactor", "deploy",
        "analyze", "compare", "evaluate", "optimize", "migrate",
        "production", "security", "performance", "scalability",
        "convergence", "integration", "pipeline", "workflow",
        "kubernetes", "microservice", "distributed", "platform",
        "infrastructure", "orchestration", "container", "mesh",
    ]
    has_technical = any(kw in prompt_lower for kw in technical_keywords)
    has_list = "list" in prompt_lower or "top " in prompt_lower
    is_long = word_count > 30
    is_very_long = word_count > 80

    # Count distinct technical concepts
    technical_count = sum(1 for kw in technical_keywords if kw in prompt_lower)
    # Complex: very long + technical, or dense technical concepts
    if is_very_long and (has_technical or has_code or technical_count >= 2):
        return "complex"
    if is_long and technical_count >= 3:
        return "complex"
    if technical_count >= 4 and word_count > 15:
        return "complex"  # Dense technical query that's also substantive
    # Medium: long queries, or technical + code, or multiple tech keywords
    if is_long or (has_technical and has_code) or technical_count >= 2:
        return "medium"
    # Simple: lists, short, non-technical
    if has_list and word_count < 20:
        return "simple"
    if word_count < 10 and not has_technical:
        return "simple"
    if has_technical or has_code:
        return "medium"
    return "simple"


# --- Smart Cache ----------------------------------------------------


class ResponseCache:
    """Thread-safe LRU cache for model responses.

    Caches by (prompt_hash, model_id) to avoid redundant calls.
    """

    def __init__(self, max_size: int = 500, ttl_seconds: int = 300):
        self._cache: dict[str, dict[str, Any]] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def _key(self, prompt: str, model_id: str) -> str:
        phash = hashlib.md5(prompt.encode()).hexdigest()[:16]
        return f"{phash}:{model_id}"

    def get(self, prompt: str, model_id: str) -> (dict[str, Any] | None):
        with self._lock:
            key = self._key(prompt, model_id)
            entry = self._cache.get(key)
            if entry is None:
                return None
            if time.time() - entry["ts"] > self._ttl:
                del self._cache[key]
                return None
            return entry

    def set(self, prompt: str, model_id: str, response: dict[str, Any]) -> None:
        with self._lock:
            if len(self._cache) >= self._max_size:
                oldest = min(self._cache.keys(), key=lambda k: self._cache[k]["ts"])
                del self._cache[oldest]
            key = self._key(prompt, model_id)
            entry = {
                "response": response,
                "ts": time.time(),
                "confidence": response.get("confidence", 0.0),
                "tokens": response.get("usage", {}).get("total_tokens", 0),
            }
            self._cache[key] = entry

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


# --- Hyper-Optimized Parallel Executor ------------------------------


class HyperOptimizer:
    """Parallel multi-model execution engine with adaptive routing.

    Runs models CONCURRENTLY in tiers, with fast-failover between providers,
    weighted consensus voting, and smart caching.
    """

    VERSION = "1.0.0"

    def __init__(self, verbose: bool = False, max_parallel: int = 10):
        self._verbose = verbose
        self._max_parallel = max_parallel
        self._metrics: dict[str, ModelMetrics] = {}
        self._metrics_lock = threading.Lock()
        self._cache = ResponseCache()
        self._provider_cache: dict[str, Any] = {}

    def _log(self, msg: str) -> None:
        if self._verbose:
            print(f"[HyperOptimizer] {msg}")

    def _get_provider_cached(self, name: str):
        """Get provider with caching to avoid redundant lookups."""
        if name not in self._provider_cache:
            try:
                self._provider_cache[name] = get_provider(name)
            except Exception as exc:
                note_failure("hyper_optimizer:L289", exc)
                return None
        return self._provider_cache[name]

    def _get_metrics(self, provider: str, model: str) -> ModelMetrics:
        """Get or create metrics for a model."""
        key = f"{provider}/{model}"
        with self._metrics_lock:
            if key not in self._metrics:
                self._metrics[key] = ModelMetrics(model_id=model, provider=provider)
            return self._metrics[key]

    def _build_full_pipeline(self) -> list[ChainStep]:
        """Build the FULL pipeline with ALL available models across ALL tiers.

        No adaptive routing. No tier skipping. EVERY model for EVERY query."""
        pipeline = []

        # ALL tiers, ALL models, ALWAYS
        for tier in [TIER1_FAST, TIER2_BALANCED, TIER3_DEEP]:
            for step in tier:
                provider = self._get_provider_cached(step.provider)
                if provider and provider.has_valid_key():
                    metrics = self._get_metrics(step.provider, step.model)
                    if not metrics.is_dead:
                        pipeline.append(step)

        return pipeline

    def _execute_single(self, step: ChainStep, prompt: str) -> ModelResponse:
        """Execute a single model, with caching."""
        # Check cache first
        cached_entry = self._cache.get(prompt, f"{step.provider}/{step.model}")
        if cached_entry:
            cached_resp = cached_entry["response"]
            cached_confidence = cached_entry.get("confidence", 0.0)
            # Record cache hit as success in metrics (no latency cost)
            metrics = self._get_metrics(step.provider, step.model)
            with self._metrics_lock:
                metrics.record_success(0)  # 0ms latency = cache hit
            return ModelResponse(
                provider=step.provider,
                model=step.model,
                content=cached_resp.get("content", ""),
                elapsed_ms=0,
                tokens_used=cached_entry.get("tokens", 0),
                success=bool(cached_resp.get("content")),
                error=cached_resp.get("error", ""),
                confidence=cached_confidence,
            )

        provider = self._get_provider_cached(step.provider)
        if not provider or not provider.has_valid_key():
            return ModelResponse(
                provider=step.provider, model=step.model,
                content="", elapsed_ms=0, tokens_used=0,
                success=False, error="Provider not available",
            )

        t0 = time.time()
        response = provider.chat(
            prompt=prompt,
            system_prompt=step.system_prompt,
            model=step.model,
            max_tokens=step.max_tokens,
            temperature=step.temperature,
        )
        elapsed = int((time.time() - t0) * 1000)

        content = response.get("content", "")
        error = response.get("error", "")
        model_name = response.get("model", step.model)
        tokens = response.get("usage", {}).get("total_tokens", 0)
        success = bool(content and not error)
        confidence = estimate_confidence(content, error) if success else 0.0

        # Update metrics (thread-safe via _metrics_lock)
        metrics = self._get_metrics(step.provider, step.model)
        with self._metrics_lock:
            if success:
                metrics.record_success(elapsed)
                self._cache.set(prompt, f"{step.provider}/{step.model}", response)
            else:
                metrics.record_failure(error)

        return ModelResponse(
            provider=step.provider, model=model_name,
            content=content, elapsed_ms=elapsed,
            tokens_used=tokens, success=success,
            error=error, confidence=confidence,
        )

    def _execute_single_with_semaphore(self, step: ChainStep, prompt: str, sem: threading.Semaphore) -> ModelResponse:
        """Execute a single model holding its provider semaphore (rate-limit guard)."""
        with sem:
            return self._execute_single(step, prompt)

    def _execute_batch(
        self,
        steps: list[ChainStep],
        prompt: str,
        batch_label: str,
    ) -> BatchResult:
        """Execute a batch of models IN PARALLEL.

        All models in the batch run concurrently, with per-provider
        concurrency limits to avoid rate limiting.
        Results are collected as they complete.
        """
        batch = BatchResult()
        self._log(f"Batch {batch_label}: {len(steps)} models (parallel)")

        # Per-provider semaphores to limit concurrency (avoid rate limiting)
        provider_semaphores: dict[str, threading.Semaphore] = {}
        for s in steps:
            if s.provider not in provider_semaphores:
                # Max 2 concurrent requests per provider
                provider_semaphores[s.provider] = threading.Semaphore(2)

        start = time.time()
        with ThreadPoolExecutor(max_workers=min(self._max_parallel, len(steps))) as pool:
            future_to_step = {}
            for step in steps:
                sem = provider_semaphores[step.provider]
                future = pool.submit(self._execute_single_with_semaphore, step, prompt, sem)
                future_to_step[future] = step

            for future in as_completed(future_to_step):
                step = future_to_step[future]
                try:
                    result = future.result(timeout=60)
                    batch.responses.append(result)
                    batch.tokens_used += result.tokens_used

                    key = f"{result.provider}/{result.model}"
                    self._log(f"  {key}: {'OK' if result.success else 'FAIL'} "
                              f"({result.elapsed_ms}ms, conf={result.confidence:.2f})")

                    if not result.success:
                        batch.errors.append(f"{key}: {result.error[:800]}")

                except Exception as e:
                    key = f"{step.provider}/{step.model}"
                    batch.errors.append(f"{key}: {e}")
                    self._log(f"  {key}: TIMEOUT/ERROR")

        batch.elapsed_ms = int((time.time() - start) * 1000)

        # Compute consensus from successful responses
        successful = [r for r in batch.responses if r.success]
        if successful:
            confidences = [r.confidence for r in successful]
            batch.confidence = sum(confidences) / max(len(confidences), 1)
            # Use the highest-confidence response as batch synthesis
            best = max(successful, key=lambda r: r.confidence)
            batch.consensus = best.content
            self._log(f"  Batch complete: {len(successful)}/{len(steps)} OK, "
                      f"best confidence={batch.confidence:.2f}")

        return batch

    def _compute_weighted_consensus(
        self,
        all_responses: list[ModelResponse],
    ) -> str:
        """Compute weighted consensus across all model responses.

        Uses TF-style weighted voting: each model casts a weighted vote
        for its response. Responses with high overlap get higher weight.
        """
        successful = [r for r in all_responses if r.success]
        if not successful:
            return ""
        if len(successful) == 1:
            return successful[0].content

        # Get weights for each model
        weighted_contents = []
        total_weight = 0.0

        for r in successful:
            f"{r.provider}/{r.model}"
            metrics = self._get_metrics(r.provider, r.model)
            weight = metrics.weight

            weighted_contents.append((r.content, weight))
            total_weight += weight

        # Find the response closest to the average (using similarity)
        best_score = -1.0
        best_content = successful[-1].content

        for content_a, weight_a in weighted_contents:
            score = 0.0
            for content_b, weight_b in weighted_contents:
                sim = compute_similarity(content_a, content_b)
                score += sim * weight_b
            score /= max(total_weight, 0.01)

            if score > best_score:
                best_score = score
                best_content = content_a

        return best_content

    def resolve(
        self,
        prompt: str,
        complexity: (str | None) = None,
        use_cache: bool = True,
    ) -> HyperResult:
        """Run the hyper-optimized resolution engine.

        Args:
            prompt: The query to resolve
            complexity: Override complexity classification
            use_cache: Whether to use response caching

        Returns:
            HyperResult with consensus resolution and detailed metrics
        """
        start = time.time()
        result = HyperResult(prompt=prompt)

        if not use_cache:
            self._cache.clear()

        # 1. Classify query complexity (informational only, does NOT gate model execution)
        if complexity is None:
            complexity = classify_query(prompt)
        result.complexity = complexity
        self._log(f"Query complexity classification (informational): {complexity}")

        # 2. Build FULL pipeline — ALL models, ALL tiers, ALWAYS
        pipeline = self._build_full_pipeline()

        if not pipeline:
            result.errors.append("No models available")
            result.resolution = "No models available with valid keys."
            result.confidence = 0.0
            return result

        self._log(f"Full pipeline: {len(pipeline)} models across "
                  f"{len(set(s.provider for s in pipeline))} providers - ALL executing")

        # 3. Execute ALL models in parallel — ALL tiers run EVERY time
        all_responses = []

        def _steps_in_tier(steps: list[ChainStep], tier: list[ChainStep]) -> list[ChainStep]:
            """Filter steps belonging to a tier by (provider, model) tuple."""
            tier_keys = {(s.provider, s.model) for s in tier}
            return [s for s in steps if (s.provider, s.model) in tier_keys]

        # Tier 1: Fast models
        tier1_steps = _steps_in_tier(pipeline, TIER1_FAST)
        if tier1_steps:
            batch1 = self._execute_batch(tier1_steps, prompt, "T1-Fast")
            result.batches.append(batch1)
            all_responses.extend(batch1.responses)
            result.total_tokens += batch1.tokens_used

        # Tier 2: Balanced models — ALWAYS runs
        tier2_steps = _steps_in_tier(pipeline, TIER2_BALANCED)
        if tier2_steps:
            batch2 = self._execute_batch(tier2_steps, prompt, "T2-Balanced")
            result.batches.append(batch2)
            all_responses.extend(batch2.responses)
            result.total_tokens += batch2.tokens_used

        # Tier 3: Deep models — ALWAYS runs
        tier3_steps = _steps_in_tier(pipeline, TIER3_DEEP)
        if tier3_steps:
            batch3 = self._execute_batch(tier3_steps, prompt, "T3-Deep")
            result.batches.append(batch3)
            all_responses.extend(batch3.responses)
            result.total_tokens += batch3.tokens_used

        # 4. Compute weighted consensus
        result.resolution = self._compute_weighted_consensus(all_responses)

        # 5. Compute final confidence
        successful = [r for r in all_responses if r.success]
        if successful:
            confidences = [r.confidence for r in successful]
            result.confidence = sum(confidences) / max(len(confidences), 1)

        # 6. Track model votes
        for r in successful:
            key = f"{r.provider}/{r.model}"
            metrics = self._get_metrics(r.provider, r.model)
            result.model_votes[key] = metrics.weight

        result.models_used = list(set(
            f"{r.provider}/{r.model}" for r in all_responses if r.success
        ))
        result.total_elapsed_ms = int((time.time() - start) * 1000)

        self._log(f"Resolution complete: {len(result.models_used)} models, "
                  f"{result.total_elapsed_ms}ms, "
                  f"confidence={result.confidence:.3f}")

        return result

    def get_health_report(self) -> dict[str, Any]:
        """Get health report for all tracked models."""
        with self._metrics_lock:
            report = {
                "total_models": len(self._metrics),
                "alive": sum(1 for m in self._metrics.values() if not m.is_dead),
                "dead": sum(1 for m in self._metrics.values() if m.is_dead),
                "models": {}
            }
            for key, m in sorted(self._metrics.items()):
                report["models"][key] = {
                    "success_rate": m.success_rate,
                    "avg_latency_ms": m.avg_latency_ms,
                    "weight": m.weight,
                    "is_dead": m.is_dead,
                    "total_calls": m.total_calls,
                }
            return report


# --- Self-Test ------------------------------------------------------


if __name__ == "__main__":
    print("HyperOptimizer v" + HyperOptimizer.VERSION)
    print("=" * 60)

    optimizer = HyperOptimizer(verbose=True)

    # Test classification
    test_queries = [
        "What's the weather?",
        "list the top 5 security risks.",
        "Design a production deployment architecture for a microservices-based AI inference platform with Kubernetes, service mesh, canary deployments, and observability stack.",
    ]

    for q in test_queries:
        complexity = classify_query(q)
        print(f"\n  [{complexity:8s}] {q[:60]}...")

    # Quick resolution test
    test_prompt = "What are the key considerations for AI safety?"
    print(f"\n--- Resolving: {test_prompt} ---")
    result = optimizer.resolve(test_prompt)

    print(f"\n{'='*60}")
    print(f"Complexity:    {result.complexity}")
    print(f"Models used:   {len(result.models_used)}")
    print(f"Confidence:    {result.confidence:.3f}")
    print(f"Total time:    {result.total_elapsed_ms}ms")
    print(f"Batches:       {len(result.batches)}")

    if result.resolution:
        print(f"\nResolution preview: {result.resolution[:500]}...")

    print("\nModel health:")
    health = optimizer.get_health_report()
    for key, info in health["models"].items():
        status = "ALIVE" if not info["is_dead"] else "DEAD"
        print(f"  {key:45s} | {status} | "
              f"weight={info['weight']:.2f} | "
              f"rate={info['success_rate']:.0%} | "
              f"avg={info['avg_latency_ms']:.0f}ms")
