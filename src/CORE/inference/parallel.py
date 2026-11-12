# coding: utf-8
"""
parallel.py — ParallelInferenceEngine for Azazael

Runs inference across ALL free providers simultaneously using Python 3.14t
free-threading. No artificial timeouts. Every provider gets all the time
it needs to respond.

Three execution modes:

    ENSEMBLE: Same prompt -> multiple providers -> combined result
    PIPELINE: Provider A -> prompt -> Provider B -> prompt -> Provider C
    BROADCAST: ALL providers -> ALL results -> sorted by quality/speed

MANDATE: No provider is denied resources. Thoroughness over speed.
         Every provider responds on its own schedule. Results arrive
         as they complete. Nothing is cancelled preemptively.

Uses FreeThreadPool from CORE.freethreading for true parallelism under
free-threading (no-GIL) Python 3.14t.

Usage:
    from CORE.inference.parallel import ParallelInferenceEngine

    engine = ParallelInferenceEngine()

    # Broadcast to ALL providers with keys
    results = engine.broadcast("What is 2+2?")

    # Ensemble: same prompt, multiple providers, vote
    result = engine.ensemble("Is Rust better than Go?",
                             providers=["groq", "mistral", "gemini"],
                             strategy="vote")

    # Pipeline: chain providers
    steps = [
        PipelineStep("groq", "Write a tweet about AI"),
        PipelineStep("mistral", "Summarize the following:\\n{previous_output}"),
    ]
    result = engine.pipeline("Hello", steps)

    # MAXIMUM INFERENCE: EVERYTHING AT ONCE
    result = engine.max_inference("What is the meaning of 42?")
"""

import os
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, List
from collections import Counter
from concurrent.futures import as_completed

from CORE.inference.providers import (
    get_provider,
    list_providers,
    PROVIDER_REGISTRY,
    _global_breaker,
)

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
try:
    from CORE.freethreading import FreeThreadPool, is_free_threaded  # pyright: ignore[reportAssignmentType] -- try-success binds the real freethreading classes while the except fallback defines structurally distinct local classes behind the same names; selection is by import success, not substitutability
except ImportError:
    import os
    from concurrent.futures import ThreadPoolExecutor
    def is_free_threaded() -> bool:
        return False
    class FreeThreadPool:
        """Fallback FreeThreadPool for Python 3.12 (wraps ThreadPoolExecutor)."""
        def __init__(self, max_workers=0, thread_name_prefix="ft-worker"):
            self.max_workers = max_workers or (os.cpu_count() or 8)
            self.thread_name_prefix = thread_name_prefix
            self._executor = None
        def __enter__(self):
            self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix=self.thread_name_prefix)
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
        @staticmethod
        def info() -> dict:
            cpu = os.cpu_count() or 8
            return {'free_threaded': False, 'cpu_count': cpu, 'recommended_workers': cpu}


# =========================================================================
# Data Types
# =========================================================================

@dataclass
class ProviderResult:
    """Result from a single provider in a parallel inference run.

    Every result is recorded regardless of success or failure.
    No provider is skipped or preemptively cancelled.
    """
    provider: str
    model: str
    content: str
    elapsed_ms: int
    success: bool
    error: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = ""

    @property
    def tokens_per_second(self) -> float:
        """Throughput: tokens / second."""
        total = self.usage.get("total_tokens", 0)
        sec = self.elapsed_ms / 1000.0
        return total / sec if sec > 0 else 0.0


@dataclass
class EnsembleResult:
    """Combined result from ensemble inference across multiple providers."""
    prompt: str
    strategy: str
    results: list[ProviderResult]
    elapsed_ms: int
    provider_count: int
    success_count: int
    failure_count: int

    consensus: str = ""
    concatenated: str = ""
    best_result: str = ""

    @property
    def all_contents(self) -> list[str]:
        return [r.content for r in self.results if r.success]

    @property
    def average_elapsed_ms(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.elapsed_ms for r in self.results) / len(self.results)

    @property
    def total_tokens(self) -> int:
        return sum(r.usage.get("total_tokens", 0) for r in self.results)

    @property
    def aggregate_throughput(self) -> float:
        sec = self.elapsed_ms / 1000.0
        return self.total_tokens / sec if sec > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "strategy": self.strategy,
            "consensus": self.consensus,
            "concatenated": self.concatenated,
            "best_result": self.best_result,
            "elapsed_ms": self.elapsed_ms,
            "provider_count": self.provider_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "total_tokens": self.total_tokens,
            "aggregate_throughput_tps": round(self.aggregate_throughput, 1),
            "results": [
                {
                    "provider": r.provider,
                    "model": r.model,
                    "content": r.content,
                    "elapsed_ms": r.elapsed_ms,
                    "success": r.success,
                    "error": r.error,
                    "tokens_per_second": round(r.tokens_per_second, 1),
                }
                for r in self.results
            ],
        }


@dataclass
class PipelineStep:
    """A single step in an inference pipeline."""
    provider: str
    prompt_template: str = ""
    model: (str | None) = None
    max_tokens: (int | None) = None
    temperature: (float | None) = None
    system_prompt: (str | None) = None

    def __post_init__(self):
        if not self.prompt_template:
            self.prompt_template = "{previous_output}"


@dataclass
class PipelineResult:
    """Result from a pipeline of inference steps."""
    steps: list[PipelineStep]
    step_results: list[ProviderResult]
    final_output: str
    elapsed_ms: int
    total_tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": len(self.steps),
            "final_output": self.final_output,
            "elapsed_ms": self.elapsed_ms,
            "total_tokens": self.total_tokens,
            "step_results": [
                {
                    "step": i,
                    "provider": r.provider,
                    "model": r.model,
                    "content": r.content,
                    "elapsed_ms": r.elapsed_ms,
                    "success": r.success,
                    "error": r.error,
                }
                for i, r in enumerate(self.step_results)
            ],
        }


@dataclass
class BroadcastResult:
    """Result from broadcasting to ALL available providers."""
    prompt: str
    results: list[ProviderResult]
    elapsed_ms: int
    total_providers_available: int
    total_providers_attempted: int
    success_count: int
    failure_count: int
    total_tokens: int
    fastest: (ProviderResult | None) = None
    slowest: (ProviderResult | None) = None
    best_by_throughput: (ProviderResult | None) = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "elapsed_ms": self.elapsed_ms,
            "total_providers_available": self.total_providers_available,
            "total_providers_attempted": self.total_providers_attempted,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "total_tokens": self.total_tokens,
            "fastest": self.fastest.provider if self.fastest else None,
            "fastest_ms": self.fastest.elapsed_ms if self.fastest else None,
            "slowest": self.slowest.provider if self.slowest else None,
            "slowest_ms": self.slowest.elapsed_ms if self.slowest else None,
            "best_throughput": self.best_by_throughput.provider if self.best_by_throughput else None,
            "best_throughput_tps": round(self.best_by_throughput.tokens_per_second, 1) if self.best_by_throughput else None,
            "results": [
                {
                    "provider": r.provider,
                    "model": r.model,
                    "content_preview": r.content[:120] + "..." if len(r.content) > 120 else r.content,
                    "elapsed_ms": r.elapsed_ms,
                    "success": r.success,
                    "error": r.error,
                    "tokens_per_second": round(r.tokens_per_second, 1),
                }
                for r in self.results
            ],
        }


# =========================================================================
# Parallel Inference Engine
# =========================================================================

class ParallelInferenceEngine:
    """
    Parallel Inference Engine -- maximum parallel inference across ALL providers.

    Uses FreeThreadPool for true parallelism under Python 3.14t free-threading.
    Thread-safe. Circuit breaker aware.

    MANDATE: No artificial timeouts. Every provider gets all the time it needs.
             Results arrive as they complete. Nothing is cancelled preemptively.

    Modes:
        broadcast()       -- Send prompt to ALL providers with valid keys simultaneously
        ensemble()        -- Same prompt to multiple providers, combine via strategy
        pipeline()        -- Chain providers sequentially: output feeds into next step
        max_inference()   -- ALL providers, ALL models, everything at once (no redundant calls)
        multi_model_      -- Multiple models from the SAME provider in parallel
          broadcast()
        cross_provider_   -- Same model across different providers
          compare()
    """

    VERSION = "1.0.0"

    def __init__(self, max_workers: int = 0):
        """
        Initialize the parallel inference engine.

        Args:
            max_workers: Max parallel workers for FreeThreadPool.
                         0 = auto = number of providers (I/O-bound, all fit).
        """
        self._lock = threading.Lock()
        self._max_workers = max_workers
        self._total_parallel_runs = 0
        self._total_ensemble_runs = 0
        self._total_pipeline_runs = 0
        self._total_tokens_aggregated = 0
        self._all_providers = self._scan_providers()

    # ------------------------------------------------------------------
    # Provider Discovery
    # ------------------------------------------------------------------

    def _scan_providers(self) -> list[dict[str, Any]]:
        return list_providers()

    def get_available_providers(self) -> list[dict[str, Any]]:
        """Providers with valid keys and closed circuits."""
        available = []
        for p in self._all_providers:
            if not p["has_api_key"]:
                continue
            if not _global_breaker.is_healthy(p["id"]):
                continue
            available.append(p)
        return available

    def get_all_providers_with_keys(self) -> list[dict[str, Any]]:
        return [p for p in self._all_providers if p["has_api_key"]]

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "version": self.VERSION,
            "free_threaded": is_free_threaded(),
            "total_providers": len(self._all_providers),
            "providers_with_keys": len(self.get_all_providers_with_keys()),
            "available_providers": len(self.get_available_providers()),
            "max_workers_configured": self._max_workers or "auto",
            "total_parallel_runs": self._total_parallel_runs,
            "total_ensemble_runs": self._total_ensemble_runs,
            "total_pipeline_runs": self._total_pipeline_runs,
            "total_tokens_aggregated": self._total_tokens_aggregated,
        }

    # ------------------------------------------------------------------
    # Single Provider Call -- No artificial timeout
    # ------------------------------------------------------------------

    def _call_provider(
        self,
        provider_id: str,
        prompt: str,
        model: (str | None) = None,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
        system_prompt: (str | None) = None,
    ) -> ProviderResult:
        """
        Call a single provider. No artificial timeout -- the provider gets
        as long as it needs to respond. Designed to run in a worker thread.

        Thread-safe: each call gets its own provider instance from the cache.
        """
        start = time.time()
        try:
            provider = get_provider(provider_id)
            if not provider.has_valid_key():
                return ProviderResult(
                    provider=provider_id,
                    model=model or "",
                    content="",
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=False,
                    error="No valid API key for " + provider_id,
                )

            result = provider.chat(
                prompt=prompt,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt=system_prompt,
            )

            elapsed = int((time.time() - start) * 1000)

            if "error" in result:
                return ProviderResult(
                    provider=provider_id,
                    model=result.get("model", model or ""),
                    content="",
                    elapsed_ms=elapsed,
                    success=False,
                    error=result["error"][:200],
                )

            usage = result.get("usage", {})
            return ProviderResult(
                provider=provider_id,
                model=result.get("model", model or ""),
                content=result.get("content", ""),
                elapsed_ms=elapsed,
                success=True,
                usage=usage,
                finish_reason=result.get("finish_reason", ""),
            )

        except Exception as e:
            note_failure("parallel:L416", e)
            elapsed = int((time.time() - start) * 1000)
            return ProviderResult(
                provider=provider_id,
                model=model or "",
                content="",
                elapsed_ms=elapsed,
                success=False,
                error=str(e)[:200],
            )

    # ------------------------------------------------------------------
    # Parallel Execution Helper -- No artificial timeouts
    # ------------------------------------------------------------------

    def _build_worker_fn(
        self,
        prompt: str,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
        system_prompt: (str | None) = None,
    ) -> Callable[[dict[str, Any]], ProviderResult]:
        """Build a closure that calls _call_provider for a given provider info dict."""
        def worker(p_info: dict[str, Any]) -> ProviderResult:
            model = os.environ.get(
                p_info["id"].upper() + "_MODEL",
                PROVIDER_REGISTRY.get(p_info["id"], {}).get("default_model", ""),
            )
            return self._call_provider(
                p_info["id"], prompt,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt=system_prompt,
            )
        return worker

    def _execute_in_parallel(
        self,
        targets: list[dict[str, Any]],
        worker_fn: Callable,
        prefix: str = "inf-par",
    ) -> list[ProviderResult]:
        """
        Execute a worker function across multiple targets in parallel.

        NO artificial timeout. Every provider gets all the time it needs.
        Results are collected as they complete. Providers that respond
        quickly are included alongside providers that take longer.

        Individual provider failures don't cascade. Each future is
        independently caught and wrapped in a ProviderResult.

        Returns list of ProviderResult sorted by speed (fastest first).
        """
        start = time.time()
        max_workers = self._max_workers or len(targets)
        results_list: list[ProviderResult] = []
        results_lock = threading.Lock()

        with FreeThreadPool(max_workers=max_workers, thread_name_prefix=prefix) as pool:
            futures = {pool.submit(worker_fn, t): t for t in targets}

            for future in as_completed(futures):
                target_info = futures[future]
                target_id = target_info.get("id", str(target_info))

                try:
                    result = future.result()
                    with results_lock:
                        results_list.append(result)
                except Exception as e:
                    note_failure("parallel:L487", e)
                    with results_lock:
                        results_list.append(ProviderResult(
                            provider=target_id,
                            model="",
                            content="",
                            elapsed_ms=int((time.time() - start) * 1000),
                            success=False,
                            error=str(e)[:200],
                        ))

        results_list.sort(key=lambda r: r.elapsed_ms)
        return results_list

    # ------------------------------------------------------------------
    # Broadcast Mode -- ALL Providers, ALL the time they need
    # ------------------------------------------------------------------

    def broadcast(
        self,
        prompt: str,
        include_providers: (list[str] | None) = None,
        exclude_providers: (list[str] | None) = None,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
        system_prompt: (str | None) = None,
        respect_circuit_breaker: bool = True,
    ) -> BroadcastResult:
        """
        Send prompt to ALL available providers in parallel.

        No timeout. Every provider gets all the time it needs.
        Results arrive as they complete. Fast providers are reported
        alongside slow providers.

        Args:
            prompt: The prompt to send.
            include_providers: If set, only these providers.
            exclude_providers: Providers to skip.
            max_tokens: Max tokens per response.
            temperature: Sampling temperature.
            system_prompt: Optional system prompt.
            respect_circuit_breaker: Skip providers with open circuits.

        Returns:
            BroadcastResult with all provider results sorted by speed.
        """
        start = time.time()

        if include_providers:
            targets = [p for p in self._all_providers if p["id"] in include_providers]
        else:
            targets = self.get_all_providers_with_keys()
            if respect_circuit_breaker:
                targets = [p for p in targets if _global_breaker.is_healthy(p["id"])]

        if exclude_providers:
            targets = [p for p in targets if p["id"] not in exclude_providers]

        if not targets:
            return BroadcastResult(
                prompt=prompt, results=[],
                elapsed_ms=int((time.time() - start) * 1000),
                total_providers_available=len(self._all_providers),
                total_providers_attempted=0,
                success_count=0, failure_count=0, total_tokens=0,
            )

        worker_fn = self._build_worker_fn(prompt, max_tokens, temperature, system_prompt)
        results_list = self._execute_in_parallel(targets, worker_fn, prefix="inf-bcast")

        elapsed_total = int((time.time() - start) * 1000)
        success_results = [r for r in results_list if r.success]
        failure_results = [r for r in results_list if not r.success]
        total_tokens = sum(r.usage.get("total_tokens", 0) for r in success_results)

        fastest = success_results[0] if success_results else None
        slowest = success_results[-1] if len(success_results) > 1 else None
        best_throughput = max(success_results, key=lambda r: r.tokens_per_second) if success_results else None

        broadcast_result = BroadcastResult(
            prompt=prompt, results=results_list,
            elapsed_ms=elapsed_total,
            total_providers_available=len(self._all_providers),
            total_providers_attempted=len(targets),
            success_count=len(success_results),
            failure_count=len(failure_results),
            total_tokens=total_tokens,
            fastest=fastest, slowest=slowest, best_by_throughput=best_throughput,
        )

        with self._lock:
            self._total_parallel_runs += 1
            self._total_tokens_aggregated += total_tokens

        return broadcast_result

    # ------------------------------------------------------------------
    # Ensemble Mode -- Same Prompt, Multiple Providers, Combined Result
    # ------------------------------------------------------------------

    def ensemble(
        self,
        prompt: str,
        providers: (list[str] | None) = None,
        strategy: str = "vote",
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
        system_prompt: (str | None) = None,
        existing_results: (list[ProviderResult] | None) = None,
    ) -> EnsembleResult:
        """
        Send the SAME prompt to multiple providers and combine results.

        If existing_results are provided (e.g., from a broadcast), they are
        used directly and no additional API calls are made.

        Strategies:
            'vote':        Majority vote on first response token.
                           Simple but effective for factual/classification tasks.
            'concatenate': Join all responses with provider attribution.
                           Good for multi-perspective brainstorming.
            'best':        Return the fastest successful response.
                           Good when any correct answer suffices.
            'debate':      All responses with source attribution.
                           Good for critical multi-perspective analysis.
        """
        start = time.time()
        strategies = ["vote", "concatenate", "best", "debate"]
        if strategy not in strategies:
            strategy = "vote"

        if existing_results is not None:
            results_list = existing_results
            elapsed_total = int((time.time() - start) * 1000)
            return self._compute_ensemble(
                prompt, strategy, results_list, elapsed_total, 0, 0,
                record_stats=False,
            )

        if providers:
            targets = [p for p in self._all_providers if p["id"] in providers]
        else:
            targets = self.get_available_providers()

        if not targets:
            return EnsembleResult(
                prompt=prompt, strategy=strategy, results=[],
                elapsed_ms=int((time.time() - start) * 1000),
                provider_count=0, success_count=0, failure_count=0,
            )

        worker_fn = self._build_worker_fn(prompt, max_tokens, temperature, system_prompt)
        results_list = self._execute_in_parallel(targets, worker_fn, prefix="inf-ens")

        elapsed_total = int((time.time() - start) * 1000)
        return self._compute_ensemble(
            prompt, strategy, results_list, elapsed_total, len(targets), 0,
            record_stats=True,
        )

    def _compute_ensemble(
        self,
        prompt: str,
        strategy: str,
        results_list: list[ProviderResult],
        elapsed_total: int,
        expected_count: int,
        depth: int,
        record_stats: bool = False,
    ) -> EnsembleResult:
        """
        Compute ensemble output from ProviderResults. No API calls --
        purely local computation based on results already collected.
        """
        success_results = [r for r in results_list if r.success]
        effective_count = expected_count or len(results_list)

        consensus = ""
        concatenated = ""
        best_content = ""

        if strategy == "vote" and success_results:
            first_tokens = [
                r.content.strip().split()[0] if r.content.strip() else ""
                for r in success_results
            ]
            non_empty = [t for t in first_tokens if t]
            if non_empty:
                counter = Counter(non_empty)
                most_common = counter.most_common(1)
                if most_common:
                    consensus = most_common[0][0]

        if strategy == "concatenate":
            parts = []
            for r in success_results:
                parts.append(
                    "[" + r.provider + " (" + r.model + ", " + str(r.elapsed_ms) + "ms)]\n" + r.content
                )
            concatenated = "\n\n---\n\n".join(parts)

        if strategy in ("best", "vote", "debate"):
            if success_results:
                fastest_result = min(success_results, key=lambda r: r.elapsed_ms)
                best_content = fastest_result.content
            elif results_list:
                best_content = "[All providers failed]"

        ensemble_result = EnsembleResult(
            prompt=prompt, strategy=strategy, results=results_list,
            elapsed_ms=elapsed_total, provider_count=effective_count,
            success_count=len(success_results),
            failure_count=len(results_list) - len(success_results),
            consensus=consensus, concatenated=concatenated, best_result=best_content,
        )

        if record_stats:
            with self._lock:
                self._total_ensemble_runs += 1
                self._total_tokens_aggregated += sum(
                    r.usage.get("total_tokens", 0) for r in success_results
                )

        return ensemble_result

    # ------------------------------------------------------------------
    # Pipeline Mode -- Chain Providers Sequentially
    # ------------------------------------------------------------------

    def pipeline(
        self,
        initial_prompt: str,
        steps: list[PipelineStep],
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
    ) -> PipelineResult:
        """
        Run an inference pipeline: output of each step feeds into the next.

        Each step's prompt_template can use {previous_output} to reference
        the output from the previous step. Steps execute sequentially
        by definition (each depends on the previous step's output).

        No artificial timeout. Each step gets all the time it needs.
        """
        start = time.time()
        if not steps:
            return PipelineResult(
                steps=[], step_results=[], final_output=initial_prompt,
                elapsed_ms=int((time.time() - start) * 1000), total_tokens=0,
            )

        current_prompt = initial_prompt
        step_results: list[ProviderResult] = []

        for i, step in enumerate(steps):
            if step.prompt_template and "{previous_output}" in step.prompt_template:
                step_prompt = step.prompt_template.replace("{previous_output}", current_prompt)
            elif step.prompt_template:
                step_prompt = step.prompt_template
            else:
                step_prompt = current_prompt

            result = self._call_provider(
                step.provider, step_prompt,
                model=step.model,
                max_tokens=step.max_tokens or max_tokens,
                temperature=step.temperature or temperature,
                system_prompt=step.system_prompt,
            )
            step_results.append(result)

            if not result.success:
                pipeline_result = PipelineResult(
                    steps=steps[:i + 1], step_results=step_results,
                    final_output=current_prompt,
                    elapsed_ms=int((time.time() - start) * 1000),
                    total_tokens=sum(r.usage.get("total_tokens", 0) for r in step_results if r.success),
                )
                with self._lock:
                    self._total_pipeline_runs += 1
                return pipeline_result

            current_prompt = result.content

        elapsed_total = int((time.time() - start) * 1000)
        total_tok = sum(r.usage.get("total_tokens", 0) for r in step_results if r.success)

        pipeline_result = PipelineResult(
            steps=steps, step_results=step_results,
            final_output=current_prompt,
            elapsed_ms=elapsed_total, total_tokens=total_tok,
        )

        with self._lock:
            self._total_pipeline_runs += 1
            self._total_tokens_aggregated += total_tok

        return pipeline_result

    # ------------------------------------------------------------------
    # MAXIMUM INFERENCE -- Everything, All at Once
    # ------------------------------------------------------------------

    def max_inference(
        self,
        prompt: str,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
        system_prompt: (str | None) = None,
    ) -> dict[str, Any]:
        """
        MAXIMUM INFERENCE -- ALL providers, ALL models, everything in parallel.

        Runs a single broadcast to ALL providers, then derives consensus,
        debate, and statistics from the broadcast results WITHOUT making
        any additional API calls.

        No timeout. Every provider gets all the time it needs.

        Returns a comprehensive dict with:
            - broadcast: All provider results
            - consensus: Majority vote derived from broadcast results
            - debate: All perspectives attributed
            - fastest: Fastest successful provider
            - best_throughput: Provider with highest tokens/second
            - stats: Aggregate performance statistics
        """
        start = time.time()

        # Broadcast to ALL providers -- the only API call batch
        broadcast_result = self.broadcast(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
        )

        broadcast_elapsed = int((time.time() - start) * 1000)
        successful_results = [r for r in broadcast_result.results if r.success]

        # Derive consensus, debate, best from broadcast results (zero API calls)
        consensus_result = self._compute_ensemble(
            prompt=prompt, strategy="vote",
            results_list=successful_results,
            elapsed_total=broadcast_elapsed,
            expected_count=len(successful_results), depth=0,
            record_stats=False,
        )

        debate_result = self._compute_ensemble(
            prompt=prompt, strategy="concatenate",
            results_list=successful_results,
            elapsed_total=broadcast_elapsed,
            expected_count=len(successful_results), depth=0,
            record_stats=False,
        )

        best_result = self._compute_ensemble(
            prompt=prompt, strategy="best",
            results_list=successful_results,
            elapsed_total=broadcast_elapsed,
            expected_count=len(successful_results), depth=0,
            record_stats=False,
        )

        elapsed_total = int((time.time() - start) * 1000)
        fastest = min(successful_results, key=lambda r: r.elapsed_ms) if successful_results else None
        best_tp = max(successful_results, key=lambda r: r.tokens_per_second) if successful_results else None

        result = {
            "mode": "MAX_INFERENCE",
            "prompt": prompt,
            "elapsed_ms": elapsed_total,
            "free_threaded": is_free_threaded(),
            "version": self.VERSION,

            "broadcast": broadcast_result.as_dict(),

            "consensus": consensus_result.consensus or best_result.best_result,
            "consensus_provider_count": consensus_result.success_count,

            "debate": debate_result.concatenated,
            "debate_provider_count": debate_result.success_count,

            "fastest_provider": fastest.provider if fastest else None,
            "fastest_elapsed_ms": fastest.elapsed_ms if fastest else None,
            "fastest_content": (fastest.content[:200] + "...") if fastest and len(fastest.content) > 200 else (fastest.content if fastest else ""),

            "best_throughput_provider": best_tp.provider if best_tp else None,
            "best_throughput_tps": round(best_tp.tokens_per_second, 1) if best_tp else None,

            "stats": {
                "providers_attempted": broadcast_result.total_providers_attempted,
                "providers_succeeded": broadcast_result.success_count,
                "providers_failed": broadcast_result.failure_count,
                "total_tokens": broadcast_result.total_tokens,
                "aggregate_throughput_tps": round(
                    broadcast_result.total_tokens / max(elapsed_total / 1000, 0.001), 1
                ),
            },

            "all_results": [
                {
                    "provider": r.provider,
                    "model": r.model,
                    "success": r.success,
                    "elapsed_ms": r.elapsed_ms,
                    "content": r.content,
                    "error": r.error,
                }
                for r in broadcast_result.results
            ],
        }

        with self._lock:
            self._total_parallel_runs += 1

        return result

    # ------------------------------------------------------------------
    # Multi-Model: Different models from the SAME provider
    # ------------------------------------------------------------------

    def multi_model_broadcast(
        self,
        provider_id: str,
        prompt: str,
        models: list[str],
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
    ) -> dict[str, Any]:
        """
        Broadcast a prompt to MULTIPLE MODELS of the SAME provider in parallel.

        Example:
            engine.multi_model_broadcast("openrouter", prompt, models=[
                "google/gemini-2.5-pro",
                "deepseek/deepseek-r1",
                "meta-llama/llama-3.3-70b-instruct",
            ])

        No timeout. Every model gets all the time it needs.
        """
        start = time.time()

        def worker(model_name: str) -> ProviderResult:
            return self._call_provider(
                provider_id, prompt,
                model=model_name,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        results_list: list[ProviderResult] = []
        max_workers = min(len(models), self._max_workers or 8)
        results_lock = threading.Lock()

        with FreeThreadPool(max_workers=max_workers, thread_name_prefix="inf-multi") as pool:
            futures = {pool.submit(worker, m): m for m in models}

            for future in as_completed(futures):
                model_name = futures[future]
                try:
                    result = future.result()
                    with results_lock:
                        results_list.append(result)
                except Exception as e:
                    note_failure("parallel:L955", e)
                    with results_lock:
                        results_list.append(ProviderResult(
                            provider=provider_id, model=model_name,
                            content="", elapsed_ms=int((time.time() - start) * 1000),
                            success=False, error=str(e)[:200],
                        ))

        results_list.sort(key=lambda r: r.elapsed_ms)
        success_results = [r for r in results_list if r.success]

        return {
            "provider": provider_id,
            "prompt": prompt,
            "models_tested": len(models),
            "models_succeeded": len(success_results),
            "elapsed_ms": int((time.time() - start) * 1000),
            "fastest_model": success_results[0].model if success_results else None,
            "fastest_ms": success_results[0].elapsed_ms if success_results else None,
            "results": [
                {
                    "model": r.model,
                    "success": r.success,
                    "elapsed_ms": r.elapsed_ms,
                    "content_preview": r.content[:800],
                    "error": r.error,
                }
                for r in results_list
            ],
        }

    # ------------------------------------------------------------------
    # Cross-Provider: Same model, different providers
    # ------------------------------------------------------------------

    def cross_provider_compare(
        self,
        model_name: str,
        prompt: str,
        providers: (list[str] | None) = None,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
    ) -> dict[str, Any]:
        """
        Test the SAME MODEL across different providers that serve it.

        Auto-detects which providers serve this model by scanning free_models.
        For example, Llama 3.3 70B may be available on Groq, OpenRouter,
        Cloudflare, and NVIDIA NIM simultaneously.

        No timeout. Every provider gets all the time it needs.
        """
        start = time.time()

        if providers is None:
            providers = []
            for pid, info in PROVIDER_REGISTRY.items():
                free_models = info.get("free_models", {})
                if model_name in free_models:
                    providers.append(pid)

        if not providers:
            return {
                "model": model_name,
                "error": "No known provider serves model '" + model_name + "'",
                "elapsed_ms": 0,
            }

        def worker(provider_id: str) -> ProviderResult:
            return self._call_provider(
                provider_id, prompt,
                model=model_name,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        results_list: list[ProviderResult] = []
        max_workers = min(len(providers), self._max_workers or 8)
        results_lock = threading.Lock()

        with FreeThreadPool(max_workers=max_workers, thread_name_prefix="inf-cmp") as pool:
            futures = {pool.submit(worker, p): p for p in providers}

            for future in as_completed(futures):
                pname = futures[future]
                try:
                    result = future.result()
                    with results_lock:
                        results_list.append(result)
                except Exception as e:
                    note_failure("parallel:L1044", e)
                    with results_lock:
                        results_list.append(ProviderResult(
                            provider=pname, model=model_name,
                            content="", elapsed_ms=int((time.time() - start) * 1000),
                            success=False, error=str(e)[:200],
                        ))

        results_list.sort(key=lambda r: r.elapsed_ms)
        success_results = [r for r in results_list if r.success]

        fastest = success_results[0] if success_results else None
        best_tp = max(success_results, key=lambda r: r.tokens_per_second) if success_results else None

        return {
            "model": model_name,
            "prompt": prompt,
            "providers_tested": len(providers),
            "providers_succeeded": len(success_results),
            "providers_failed": len(results_list) - len(success_results),
            "elapsed_ms": int((time.time() - start) * 1000),
            "fastest_provider": fastest.provider if fastest else None,
            "fastest_elapsed_ms": fastest.elapsed_ms if fastest else None,
            "best_throughput_provider": best_tp.provider if best_tp else None,
            "best_throughput_tps": round(best_tp.tokens_per_second, 1) if best_tp else None,
            "by_speed": [
                {
                    "provider": r.provider,
                    "success": r.success,
                    "elapsed_ms": r.elapsed_ms,
                    "tokens_per_second": round(r.tokens_per_second, 1),
                    "content_preview": r.content[:800] + ("..." if len(r.content) > 100 else ""),
                    "error": r.error,
                }
                for r in results_list
            ],
        }

    # ------------------------------------------------------------------
    # Report: Comprehensive Status
    # ------------------------------------------------------------------

    def report(self) -> dict[str, Any]:
        """Generate a comprehensive report of all providers and their status."""
        available = self.get_available_providers()
        all_with_keys = self.get_all_providers_with_keys()
        provider_details = []

        for p in self._all_providers:
            healthy = _global_breaker.is_healthy(p["id"])
            provider_details.append({
                "id": p["id"],
                "name": p["name"],
                "has_key": p["has_api_key"],
                "healthy": healthy,
                "models": p["models"],
            })

        return {
            "engine_version": self.VERSION,
            "free_threaded": is_free_threaded(),
            "total_providers": len(self._all_providers),
            "providers_with_keys": len(all_with_keys),
            "providers_available_and_healthy": len(available),
            "providers": provider_details,
            "usage": {
                "total_parallel_runs": self._total_parallel_runs,
                "total_ensemble_runs": self._total_ensemble_runs,
                "total_pipeline_runs": self._total_pipeline_runs,
                "total_tokens_aggregated": self._total_tokens_aggregated,
            },
            "max_inference_ready": len(available) >= 2,
        }

    def __repr__(self) -> str:
        a = self.get_available_providers()
        return ("ParallelInferenceEngine(" + str(len(a)) + " available providers, "
                "free-threaded=" + str(is_free_threaded()) + ")")
