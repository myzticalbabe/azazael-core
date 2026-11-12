# coding: utf-8
"""
threaded.py — ThreadedInferenceEngine for Azazael

Wraps ProviderRegistry (get_provider, list_providers) with
concurrent.futures.ThreadPoolExecutor for parallel provider queries.

Simpler, more focused alternative to ParallelInferenceEngine.
No ensemble/pipeline/max_inference complexity — just parallel queries
with configurable timeouts and per-provider model overrides.

Usage:
    from CORE.inference.threaded import ThreadedInferenceEngine

    engine = ThreadedInferenceEngine()

    # Query ALL providers with keys in parallel
    results = engine.query_all("Analyze this code")

    # Query specific providers only
    results = engine.query(["groq", "mistral", "github"],
                           "What is the capital of France?")

    # Query a single provider
    result = engine.query_single("groq", "Hello!")

    # List all providers with status
    for p in engine.get_providers():
        print(f"{p['name']}: key={'yes' if p['has_key'] else 'no'},
                         healthy={p['healthy']}")
"""

import os
import time
import threading
from typing import Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout

from CORE.inference.providers import (
    get_provider,
    list_providers,
    PROVIDER_REGISTRY,
    _global_breaker,
)
from CORE.inference.parallel import ProviderResult

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class ThreadedInferenceEngine:
    """
    Threaded Inference Engine — wraps ProviderRegistry with ThreadPoolExecutor
    for parallel provider queries.

    Unlike ParallelInferenceEngine (which focuses on ensemble/pipeline/max
    modes), this engine provides a simple API for broadcasting prompts to
    one or more providers in parallel. It uses concurrent.futures directly
    and supports per-provider timeouts.

    Key differences from ParallelInferenceEngine:
    - Uses ThreadPoolExecutor directly (not FreeThreadPool)
    - Simpler API: query_all / query / query_single
    - Configurable per-provider timeout via timeout_seconds
    - Direct model override per provider via provider_overrides dict
    - All methods return the same ProviderResult type

    NOTE on timeouts: The existing ParallelInferenceEngine has a "no artificial
    timeout" mandate. This engine provides optional timeouts. When a timeout
    fires, the provider is interrupted mid-response — the result may be
    truncated or empty. Use timeouts when you need guaranteed response times
    at the cost of completeness.

    Thread-safe. Circuit breaker aware.
    """

    VERSION = "1.0.0"

    def __init__(
        self,
        max_workers: int = 0,
        timeout_seconds: (float | None) = None,
        provider_overrides: (dict[str, dict[str, Any]] | None) = None,
    ):
        """
        Initialize the threaded inference engine.

        Args:
            max_workers: Max parallel workers for ThreadPoolExecutor.
                         0 = auto = number of providers being queried.
            timeout_seconds: Optional per-provider timeout.
                             None = no timeout (wait indefinitely).
            provider_overrides: Optional dict of {provider_id: {param: value}}.
                                Overrides model, max_tokens, temperature, etc.
                                per provider. Example:
                                {"groq": {"model": "llama-3.3-70b-versatile"}}
        """
        self._lock = threading.Lock()
        self._max_workers = max_workers
        self._timeout_seconds = timeout_seconds
        self._provider_overrides = provider_overrides or {}
        self._total_queries: int = 0
        self._total_tokens: int = 0
        self._all_providers = self._scan_providers()

    # ------------------------------------------------------------------
    # Provider Discovery
    # ------------------------------------------------------------------

    def _scan_providers(self) -> list[dict[str, Any]]:
        """Scan all known providers from the registry."""
        return list_providers()

    def get_providers(self) -> list[dict[str, Any]]:
        """list ALL known providers with their current status.

        Returns list of dicts with keys:
            id, name, description, has_api_key, healthy, models
        """
        provider_details = []
        for p in self._all_providers:
            healthy = _global_breaker.is_healthy(p["id"])
            provider_details.append({
                "id": p["id"],
                "name": p["name"],
                "description": p["description"],
                "has_key": p["has_api_key"],
                "healthy": healthy,
                "models": p.get("models", []),
            })
        return provider_details

    def get_available_providers(self) -> list[dict[str, Any]]:
        """Providers with valid keys AND healthy circuits (ready to query)."""
        available = []
        for p in self._all_providers:
            if not p["has_api_key"]:
                continue
            if not _global_breaker.is_healthy(p["id"]):
                continue
            available.append(p)
        return available

    def get_all_providers_with_keys(self) -> list[dict[str, Any]]:
        """All providers that have API keys (regardless of circuit health)."""
        return [p for p in self._all_providers if p["has_api_key"]]

    @property
    def summary(self) -> dict[str, Any]:
        """Engine summary with provider counts and usage stats."""
        with self._lock:
            return {
                "version": self.VERSION,
                "total_providers": len(self._all_providers),
                "providers_with_keys": len(self.get_all_providers_with_keys()),
                "available_providers": len(self.get_available_providers()),
                "max_workers": self._max_workers or "auto",
                "timeout_seconds": self._timeout_seconds,
                "total_queries": self._total_queries,
                "total_tokens": self._total_tokens,
            }

    # ------------------------------------------------------------------
    # Single Provider Call
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
        Call a single provider. Thread-safe — runs in a worker thread.
        Records success/failure in circuit breaker.

        Returns ProviderResult regardless of success or failure.
        """
        start = time.time()
        try:
            provider = get_provider(provider_id)

            # Check health via circuit breaker
            if not _global_breaker.allow_request(provider_id):
                return ProviderResult(
                    provider=provider_id,
                    model=model or "",
                    content="",
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=False,
                    error=f"Circuit breaker OPEN for {provider_id}",
                )

            if not provider.has_valid_key():
                return ProviderResult(
                    provider=provider_id,
                    model=model or "",
                    content="",
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=False,
                    error=f"No valid API key for {provider_id}",
                )

            # Apply provider overrides
            overrides = self._provider_overrides.get(provider_id, {})
            resolved_model = overrides.get("model") or model
            resolved_max_tokens = overrides.get("max_tokens") or max_tokens
            resolved_temperature = overrides.get("temperature") or temperature
            resolved_system_prompt = overrides.get("system_prompt") or system_prompt

            # Resolve default model from env var or registry
            if not resolved_model:
                resolved_model = os.environ.get(
                    f"{provider_id.upper()}_MODEL",
                    PROVIDER_REGISTRY.get(provider_id, {}).get("default_model", ""),
                )

            result = provider.chat(
                prompt=prompt,
                model=resolved_model,
                max_tokens=resolved_max_tokens,
                temperature=resolved_temperature,
                system_prompt=resolved_system_prompt,
            )

            elapsed = int((time.time() - start) * 1000)

            if "error" in result:
                _global_breaker.record_failure(provider_id, str(result["error"]))
                return ProviderResult(
                    provider=provider_id,
                    model=result.get("model", resolved_model or ""),
                    content="",
                    elapsed_ms=elapsed,
                    success=False,
                    error=result["error"][:200],
                )

            # Record success in circuit breaker
            _global_breaker.record_success(provider_id)

            usage = result.get("usage", {})
            return ProviderResult(
                provider=provider_id,
                model=result.get("model", resolved_model or ""),
                content=result.get("content", ""),
                elapsed_ms=elapsed,
                success=True,
                usage=usage,
                finish_reason=result.get("finish_reason", ""),
            )

        except Exception as e:
            note_failure("threaded:L253", e)
            elapsed = int((time.time() - start) * 1000)
            _global_breaker.record_failure(provider_id, str(e))
            return ProviderResult(
                provider=provider_id,
                model=model or "",
                content="",
                elapsed_ms=elapsed,
                success=False,
                error=str(e)[:200],
            )

    # ------------------------------------------------------------------
    # Parallel Execution
    # ------------------------------------------------------------------

    def _execute_in_parallel(
        self,
        targets: list[dict[str, Any]],
        prompt: str,
        max_workers: (int | None) = None,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
        system_prompt: (str | None) = None,
        timeout_seconds: (float | None) = None,
    ) -> list[ProviderResult]:
        """
        Execute provider queries in parallel using ThreadPoolExecutor.

        Individual provider failures don't cascade — each future is
        independently caught and wrapped in a ProviderResult.

        Args:
            targets: list of provider info dicts to query.
            prompt: The prompt to send.
            max_tokens: Max tokens per response.
            temperature: Sampling temperature.
            system_prompt: Optional system prompt.
            timeout_seconds: Per-provider timeout. None = no timeout.

        Returns:
            list of ProviderResult sorted by speed (fastest first).
        """
        start = time.time()
        workers = max_workers if max_workers is not None else (self._max_workers or len(targets))
        effective_timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        results_list: list[ProviderResult] = []
        results_lock = threading.Lock()

        def worker(p_info: dict[str, Any]) -> ProviderResult:
            return self._call_provider(
                p_info["id"],
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt=system_prompt,
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(worker, t): t for t in targets}

            for future in as_completed(futures):
                target_info = futures[future]
                target_id = target_info.get("id", str(target_info))

                try:
                    result = future.result(timeout=effective_timeout)
                    with results_lock:
                        results_list.append(result)
                except FutureTimeout as exc:
                    note_failure("threaded:L322", exc)
                    with results_lock:
                        results_list.append(ProviderResult(
                            provider=target_id,
                            model="",
                            content="",
                            elapsed_ms=int((time.time() - start) * 1000),
                            success=False,
                            error=f"Timeout after {effective_timeout}s",
                        ))
                except Exception as e:
                    note_failure("threaded:L332", e)
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
    # Public API
    # ------------------------------------------------------------------

    def query_all(
        self,
        prompt: str,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
        system_prompt: (str | None) = None,
        max_workers: (int | None) = None,
        timeout_seconds: (float | None) = None,
        respect_circuit_breaker: bool = True,
    ) -> dict[str, Any]:
        """
        Query ALL providers with valid keys in parallel.

        Args:
            prompt: The prompt to send.
            max_tokens: Max tokens per response.
            temperature: Sampling temperature.
            system_prompt: Optional system prompt.
            max_workers: Override max parallel workers for this call.
            timeout_seconds: Override per-provider timeout for this call.
            respect_circuit_breaker: If True, skip providers with open circuits.

        Returns:
            dict with:
                prompt: Original prompt
                total_providers_attempted: int
                providers_succeeded: int
                providers_failed: int
                total_tokens: int
                total_elapsed_ms: int
                fastest: provider_id or None
                fastest_ms: int or None
                results: list[ProviderResult] sorted by speed
                as_dicts: Same results as plain dicts for serialization
        """
        start = time.time()

        targets = self.get_all_providers_with_keys()
        if respect_circuit_breaker:
            targets = [p for p in targets if _global_breaker.is_healthy(p["id"])]

        if not targets:
            return {
                "prompt": prompt,
                "total_providers_attempted": 0,
                "providers_succeeded": 0,
                "providers_failed": 0,
                "total_tokens": 0,
                "total_elapsed_ms": int((time.time() - start) * 1000),
                "fastest": None,
                "fastest_ms": None,
                "results": [],
                "as_dicts": [],
            }

        timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        workers = max_workers if max_workers is not None else (self._max_workers or len(targets))

        results_list = self._execute_in_parallel(
            targets, prompt,
            max_workers=workers,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
            timeout_seconds=timeout,
        )

        elapsed_total = int((time.time() - start) * 1000)
        success_results = [r for r in results_list if r.success]
        failure_results = [r for r in results_list if not r.success]
        total_tokens = sum(r.usage.get("total_tokens", 0) for r in success_results)

        fastest = min(success_results, key=lambda r: r.elapsed_ms) if success_results else None

        with self._lock:
            self._total_queries += 1
            self._total_tokens += total_tokens

        return {
            "prompt": prompt,
            "total_providers_attempted": len(targets),
            "providers_succeeded": len(success_results),
            "providers_failed": len(failure_results),
            "total_tokens": total_tokens,
            "total_elapsed_ms": elapsed_total,
            "fastest": fastest.provider if fastest else None,
            "fastest_ms": fastest.elapsed_ms if fastest else None,
            "results": results_list,
            "as_dicts": [
                {
                    "provider": r.provider,
                    "model": r.model,
                    "content_preview": r.content[:200] + "..." if len(r.content) > 200 else r.content,
                    "elapsed_ms": r.elapsed_ms,
                    "success": r.success,
                    "error": r.error,
                    "tokens_per_second": round(r.tokens_per_second, 1),
                }
                for r in results_list
            ],
        }

    def query(
        self,
        provider_ids: list[str],
        prompt: str,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
        system_prompt: (str | None) = None,
        max_workers: (int | None) = None,
        timeout_seconds: (float | None) = None,
    ) -> dict[str, Any]:
        """
        Query SPECIFIC providers in parallel.

        Args:
            provider_ids: list of provider IDs to query (e.g., ["groq", "mistral"]).
            prompt: The prompt to send.
            max_tokens: Max tokens per response.
            temperature: Sampling temperature.
            system_prompt: Optional system prompt.
            max_workers: Override max parallel workers for this call.
            timeout_seconds: Override per-provider timeout for this call.

        Returns:
            Same dict structure as query_all().
        """
        targets = [p for p in self._all_providers if p["id"] in provider_ids]
        if not targets:
            return {
                "prompt": prompt,
                "total_providers_attempted": 0,
                "providers_succeeded": 0,
                "providers_failed": 0,
                "total_tokens": 0,
                "total_elapsed_ms": 0,
                "fastest": None,
                "fastest_ms": None,
                "results": [],
                "as_dicts": [],
                "error": f"No known providers match: {provider_ids}",
            }

        timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        workers = max_workers if max_workers is not None else (self._max_workers or len(targets))

        results_list = self._execute_in_parallel(
            targets, prompt,
            max_workers=workers,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
            timeout_seconds=timeout,
        )

        elapsed_total = max((r.elapsed_ms for r in results_list), default=0)
        success_results = [r for r in results_list if r.success]
        total_tokens = sum(r.usage.get("total_tokens", 0) for r in success_results)
        fastest = min(success_results, key=lambda r: r.elapsed_ms) if success_results else None

        with self._lock:
            self._total_queries += 1
            self._total_tokens += total_tokens

        return {
            "prompt": prompt,
            "providers_requested": provider_ids,
            "total_providers_attempted": len(targets),
            "providers_succeeded": len(success_results),
            "providers_failed": len(results_list) - len(success_results),
            "total_tokens": total_tokens,
            "total_elapsed_ms": elapsed_total,
            "fastest": fastest.provider if fastest else None,
            "fastest_ms": fastest.elapsed_ms if fastest else None,
            "results": results_list,
            "as_dicts": [
                {
                    "provider": r.provider,
                    "model": r.model,
                    "content_preview": r.content[:200] + "..." if len(r.content) > 200 else r.content,
                    "elapsed_ms": r.elapsed_ms,
                    "success": r.success,
                    "error": r.error,
                    "tokens_per_second": round(r.tokens_per_second, 1),
                }
                for r in results_list
            ],
        }

    def query_single(
        self,
        provider_id: str,
        prompt: str,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
        system_prompt: (str | None) = None,
        timeout_seconds: (float | None) = None,
    ) -> ProviderResult:
        """
        Query a SINGLE provider (sequential, not parallel).

        This is a convenience method. For single-provider queries it's
        more efficient than setting up a ThreadPoolExecutor.

        Returns a ProviderResult directly (not wrapped in a dict).

        Args:
            provider_id: Provider ID to query (e.g., "groq").
            prompt: The prompt to send.
            max_tokens: Max tokens per response.
            temperature: Sampling temperature.
            system_prompt: Optional system prompt.
            timeout_seconds: Optional per-provider timeout.

        Returns:
            ProviderResult with the response or error.
        """
        if provider_id not in [p["id"] for p in self._all_providers]:
            return ProviderResult(
                provider=provider_id,
                model="",
                content="",
                elapsed_ms=0,
                success=False,
                error=f"Unknown provider: {provider_id}",
            )

        # Use the same _call_provider method for consistency
        # (circuit breaker, overrides, error handling all apply)
        if timeout_seconds is not None:
            result = self._call_with_timeout(
                provider_id, prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt=system_prompt,
                timeout=timeout_seconds,
            )
        else:
            result = self._call_provider(
                provider_id, prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt=system_prompt,
            )

        with self._lock:
            self._total_queries += 1
            if result.success:
                self._total_tokens += result.usage.get("total_tokens", 0)

        return result

    def _call_with_timeout(
        self,
        provider_id: str,
        prompt: str,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
        system_prompt: (str | None) = None,
        timeout: float = 30.0,
    ) -> ProviderResult:
        """
        Call a single provider with a timeout using ThreadPoolExecutor.

        If the provider doesn't respond within `timeout` seconds, returns
        a ProviderResult with success=False and a timeout error.
        """
        start = time.time()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                self._call_provider,
                provider_id, prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt=system_prompt,
            )
            try:
                return future.result(timeout=timeout)
            except TimeoutError as exc:
                note_failure("threaded:L628", exc)
                return ProviderResult(
                    provider=provider_id,
                    model="",
                    content="",
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=False,
                    error=f"Timeout after {timeout}s",
                )
            except Exception as e:
                note_failure("threaded:L637", e)
                return ProviderResult(
                    provider=provider_id,
                    model="",
                    content="",
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=False,
                    error=str(e)[:200],
                )

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def report(self) -> dict[str, Any]:
        """
        Generate a comprehensive report of all providers and engine usage.

        Returns dict with provider details, engine version, usage stats.
        """
        return {
            "engine": "ThreadedInferenceEngine",
            "version": self.VERSION,
            "provider_overrides": list(self._provider_overrides.keys()),
            "timeout_seconds": self._timeout_seconds,
            **self.summary,
        }

    def __repr__(self) -> str:
        a = self.get_available_providers()
        timeout_str = f", timeout={self._timeout_seconds}s" if self._timeout_seconds else ""
        return (
            f"ThreadedInferenceEngine({len(a)} available providers"
            f"{timeout_str})"
        )
