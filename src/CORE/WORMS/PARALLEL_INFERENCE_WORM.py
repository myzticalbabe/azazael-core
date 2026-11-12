# coding: utf-8
"""
PARALLEL_INFERENCE_WORM.py — Parallel Inference Worm for Azazael

Wraps ParallelInferenceEngine as a registered worm. Provides unified
access to ALL inference providers (21 free, 14 with active keys) plus
local models via a single interface.

Three layers:
1. Single inference (via engine.broadcast / engine.ensemble)
2. Local model benchmarking (Ollama, LlamaCpp, HuggingFace direct)
3. MAX_INFERENCE — everything at once, all providers, all models

All results recorded to LocalMemoryStore and published to MemoryBus.

Usage:
    from CORE.WORMS.PARALLEL_INFERENCE_WORM import ParallelInferenceWorm
    worm = ParallelInferenceWorm()
    result = worm.execute(prompt="What is 2+2?", strategy="broadcast")
    benchmark = worm.benchmark_local("What language are you?")
    max_result = worm.max_inference("Analyze this codebase")
"""

from typing import Any, List
import time
import threading
import subprocess
import json
import urllib.request
from pathlib import Path

from CORE.worm_registry import register_worm
from CORE.inference.parallel import (
    ParallelInferenceEngine,
    PipelineStep,
    BroadcastResult,
    EnsembleResult,
)
from CORE.inference.bus_utils import InferenceBusPublisher
from CORE.direct_memory import DirectMemory
from CORE.inference.providers import get_provider
try:
    from CORE.freethreading import FreeThreadPool  # pyright: ignore[reportAssignmentType] -- try-success binds the real freethreading class while the except fallback defines a structurally distinct local class behind the same name; selection is by import success, not substitutability
except ImportError:
    import os
    from concurrent.futures import ThreadPoolExecutor
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
from concurrent.futures import as_completed

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@register_worm(
    category="analysis",
    priority=8,
    cooldown_seconds=15.0,
    description="Parallel inference — ALL providers simultaneously, local model benchmarking, session recording"
)
class ParallelInferenceWorm:
    """
    Parallel Inference Worm — maximum inference across ALL providers.

    Wraps ParallelInferenceEngine as a worm with MemoryBus publishing
    and LocalMemoryStore recording for every action.

    Features:
    - broadcast(): ALL remote providers simultaneously
    - ensemble(): Same prompt to multiple providers with voting
    - pipeline(): Chain providers sequentially
    - benchmark_local(): ALL local models benchmarked in parallel
    - max_inference(): Everything at once, zero redundant API calls
    - multi_model(): Same provider, multiple models in parallel
    - cross_compare(): Same model, different providers

    All actions recorded to LocalMemoryStore for session audit trail.
    """

    VERSION = "1.0.0"

    def __init__(self, dm: (DirectMemory | None) = None):
        self._engine = ParallelInferenceEngine()
        self._lock = threading.Lock()
        self._exec_count: int = 0
        self._total_tokens: int = 0
        self._total_local_benchmarks: int = 0

        # Recording via DirectMemory (CANONICAL)
        self._dm = dm or DirectMemory(session_id="parallel_inference_worm")
        self._bus_pub = InferenceBusPublisher(source="parallel_inference_worm")

        # Record worm init
        self._record("worm.init", {"version": self.VERSION}, tags=["worm", "startup"])

    # ------------------------------------------------------------------
    # Recording helpers
    # ------------------------------------------------------------------

    def _record(self, event_type: str, data: dict[str, Any],
                 severity: str = "info", tags: (list[str] | None) = None):
        """Record an event to DirectMemory (canonical memory system)."""
        try:
            self._dm.record(
                event_type,
                data=data,
                severity=severity,
                tags=tags or [],
                source="parallel_inference_worm",
            )
        except Exception as exc:
            note_failure("PARALLEL_INFERENCE_WORM:L127", exc) # Recording is best-effort

    def _publish(self, data: dict[str, Any]):
        """Publish to MemoryBus."""
        try:
            self._bus_pub.publish(data)
        except Exception as exc:
            note_failure("PARALLEL_INFERENCE_WORM:L134", exc)

    # ------------------------------------------------------------------
    # Single inference — unified entry point
    # ------------------------------------------------------------------

    def execute(
        self,
        prompt: str = "",
        strategy: str = "broadcast",
        providers: (list[str] | None) = None,
        models: (list[str] | None) = None,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
        system_prompt: (str | None) = None,
    ) -> dict[str, Any]:
        """
        Execute inference. Auto-selects mode based on strategy.

        Args:
            prompt: The prompt to send.
            strategy: 'broadcast' (ALL providers), 'ensemble' (with voting),
                      'pipeline' (chain), 'max' (everything).
            providers: Specific providers (ensemble mode).
            models: Specific models (multi-model mode).
            max_tokens: Max tokens per response.
            temperature: Sampling temperature.
            system_prompt: Optional system prompt.

        Returns:
            dict with results, stats, and metadata.
        """
        start = time.time()
        if not prompt:
            result = {"success": False, "error": "No prompt provided", "elapsed_ms": 0}
            self._record("execute.error", {"error": "No prompt", "strategy": strategy}, severity="error")
            return result

        try:
            if strategy == "max" or strategy == "max_inference":
                raw = self._engine.max_inference(prompt, max_tokens, temperature, system_prompt)
                result = self._format_max_result(raw)

            elif strategy == "ensemble":
                ens = self._engine.ensemble(
                    prompt, providers=providers, strategy="vote",
                    max_tokens=max_tokens, temperature=temperature,
                    system_prompt=system_prompt,
                )
                result = self._format_ensemble_result(ens)

            elif strategy == "pipeline":
                if not providers or len(providers) < 2:
                    result = {"success": False, "error": "Pipeline needs >=2 providers"}
                else:
                    steps = [
                        PipelineStep(p, "" if i == 0 else "{previous_output}")
                        for i, p in enumerate(providers)
                    ]
                    pipe = self._engine.pipeline(prompt, steps, max_tokens, temperature)
                    result = self._format_pipeline_result(pipe)

            elif strategy == "multi_model":
                if not providers or not models:
                    result = {"success": False, "error": "multi_model needs providers + models"}
                else:
                    mm = self._engine.multi_model_broadcast(
                        providers[0], prompt, models,
                        max_tokens, temperature,
                    )
                    result = mm
                    result["success"] = mm.get("models_succeeded", 0) > 0

            elif strategy == "cross_compare":
                if not models:
                    result = {"success": False, "error": "cross_compare needs at least one model"}
                elif not providers:
                    cc = self._engine.cross_provider_compare(
                        models[0], prompt,
                        max_tokens=max_tokens, temperature=temperature,
                    )
                    result = cc
                    result["success"] = cc.get("providers_succeeded", 0) > 0
                else:
                    cc = self._engine.cross_provider_compare(
                        models[0], prompt, providers=providers,
                        max_tokens=max_tokens, temperature=temperature,
                    )
                    result = cc
                    result["success"] = cc.get("providers_succeeded", 0) > 0

            else:
                # Default: broadcast to ALL
                bcast = self._engine.broadcast(
                    prompt, include_providers=providers,
                    max_tokens=max_tokens, temperature=temperature,
                    system_prompt=system_prompt,
                )
                result = self._format_broadcast_result(bcast)

            elapsed = int((time.time() - start) * 1000)
            result["elapsed_ms"] = elapsed

            # Record success
            with self._lock:
                self._exec_count += 1
                tokens = result.get("stats", {}).get("total_tokens", 0) or \
                         result.get("total_tokens", 0)
                self._total_tokens += tokens

            self._record(
                "execute.complete",
                {"strategy": strategy, "prompt": prompt[:800],
                 "providers": providers, "elapsed_ms": elapsed},
                tags=["inference", strategy],
            )
            self._publish({
                "event": "inference.complete",
                "strategy": strategy,
                "elapsed_ms": elapsed,
            })

        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            result = {"success": False, "error": str(e)[:200], "elapsed_ms": elapsed}
            self._record(
                "execute.error",
                {"strategy": strategy, "error": str(e)[:200]},
                severity="error",
            )

        return result

    # ------------------------------------------------------------------
    # Local model benchmarking
    # ------------------------------------------------------------------

    def benchmark_local(
        self,
        prompt: str = "Reply with EXACTLY one word describing your model.",
        max_tokens: int = 10,
        temperature: float = 0,
    ) -> dict[str, Any]:
        """
        Benchmark ALL local models on device in parallel.

        Tests: Ollama (auto-discovered models), Ollama Native, LlamaCppDirect,
        HuggingFaceDirect. Every model gets all the time it needs.

        Auto-discovers Ollama models at runtime via `ollama list` command.
        Falls back to known model list if ollama command not available.

        Returns:
            dict with per-model results sorted by speed, aggregate stats.
        """
        start = time.time()

        # Gather all local provider + model combinations
        local_tasks: list[dict[str, Any]] = []

        # -- Auto-detect Ollama models at runtime --
        ollama_models: list[str] = []
        try:
            result = subprocess.run(
                ["ollama", "list"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                for line in lines[1:]:  # Skip header
                    parts = line.split()
                    if parts:
                        name = parts[0].strip()
                        if name:
                            ollama_models.append(name)
        except (FileNotFoundError, Exception) as exc:
            note_failure("PARALLEL_INFERENCE_WORM:L309", exc)

        # Fallback: known models from scan
        if not ollama_models:
            ollama_models = [
                "gemma2:2b", "gemma3:4b", "llama3.2:1b",
                "eve-unleashed:latest", "llava:7b",
                "minicpm-v:latest", "moondream:latest",
            ]

        for m in ollama_models:
            local_tasks.append(
                {"provider": "ollama_native", "model": m, "desc": f"Ollama {m}"}
            )

        # Ollama via OpenAI-compatible
        local_tasks.append(
            {"provider": "ollama", "model": "llama3.2:1b",
             "desc": "Ollama (OpenAI compat)"}
        )

        # LlamaCppDirect (in-process GGUF)
        local_tasks.append(
            {"provider": "llama_cpp_direct", "model": "llama3.2:1b",
             "desc": "LlamaCpp Direct (GGUF)"}
        )

        # HuggingFaceDirect (in-process transformers)
        local_tasks.append(
            {"provider": "huggingface_direct", "model": "meta-llama/Llama-3.2-1B",
             "desc": "HuggingFace Direct (transformers)"}
        )

        self._record(
            "benchmark_local.start",
            {"prompt": prompt[:800], "models": len(local_tasks),
             "auto_detected_ollama": len(ollama_models)},
            tags=["benchmark", "local"],
        )

        # Execute ALL local models in parallel (no timeouts)
        results_list: list[dict[str, Any]] = []
        results_lock = threading.Lock()

        def worker(task: dict[str, Any]) -> dict[str, Any]:
            t_start = time.time()
            try:
                provider = get_provider(task["provider"])
                r = provider.chat(
                    prompt=prompt,
                    model=task["model"],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                elapsed = int((time.time() - t_start) * 1000)
                if "error" in r:
                    return {
                        "provider": task["provider"],
                        "model": task["model"],
                        "desc": task["desc"],
                        "success": False,
                        "error": r["error"][:200],
                        "elapsed_ms": elapsed,
                    }
                return {
                    "provider": task["provider"],
                    "model": task["model"],
                    "desc": task["desc"],
                    "success": True,
                    "content": r.get("content", ""),
                    "elapsed_ms": elapsed,
                    "usage": r.get("usage", {}),
                    "finish_reason": r.get("finish_reason", ""),
                }
            except Exception as e:
                note_failure("PARALLEL_INFERENCE_WORM:L384", e)
                return {
                    "provider": task["provider"],
                    "model": task["model"],
                    "desc": task["desc"],
                    "success": False,
                    "error": str(e)[:200],
                    "elapsed_ms": int((time.time() - t_start) * 1000),
                }

        with FreeThreadPool(max_workers=len(local_tasks), thread_name_prefix="bm-local") as pool:
            futures = {pool.submit(worker, t): t["desc"] for t in local_tasks}
            for future in as_completed(futures):
                try:
                    r = future.result()
                    with results_lock:
                        results_list.append(r)
                except Exception as e:
                    note_failure("PARALLEL_INFERENCE_WORM:L401", e)
                    with results_lock:
                        results_list.append({
                            "provider": "unknown",
                            "model": "unknown",
                            "desc": futures[future],
                            "success": False,
                            "error": str(e)[:200],
                            "elapsed_ms": 0,
                        })

        # Sort by speed
        results_list.sort(key=lambda r: r.get("elapsed_ms", 0))
        success_results = [r for r in results_list if r["success"]]
        failure_results = [r for r in results_list if not r["success"]]

        elapsed_total = int((time.time() - start) * 1000)
        fastest = success_results[0] if success_results else None
        slowest = success_results[-1] if len(success_results) > 1 else None

        benchmark_result = {
            "strategy": "benchmark_local",
            "prompt": prompt[:200],
            "total_models_attempted": len(local_tasks),
            "total_models_succeeded": len(success_results),
            "total_models_failed": len(failure_results),
            "elapsed_ms": elapsed_total,
            "fastest_model": f"{fastest['provider']}/{fastest['model']}" if fastest else None,
            "fastest_ms": fastest["elapsed_ms"] if fastest else None,
            "slowest_model": f"{slowest['provider']}/{slowest['model']}" if slowest else None,
            "slowest_ms": slowest["elapsed_ms"] if slowest else None,
            "results": results_list,

            # Summary by speed tier
            "under_1s": [r["desc"] for r in success_results if r["elapsed_ms"] < 1000],
            "under_5s": [r["desc"] for r in success_results if 1000 <= r["elapsed_ms"] < 5000],
            "over_5s": [r["desc"] for r in success_results if r["elapsed_ms"] >= 5000],
        }

        with self._lock:
            self._total_local_benchmarks += 1

        self._record(
            "benchmark_local.complete",
            {
                "attempted": len(local_tasks),
                "succeeded": len(success_results),
                "failed": len(failure_results),
                "elapsed_ms": elapsed_total,
                "fastest": benchmark_result["fastest_model"],
            },
            tags=["benchmark", "local"],
        )
        self._publish({
            "event": "benchmark_local.complete",
            "succeeded": len(success_results),
            "failed": len(failure_results),
        })

        return benchmark_result

    # ------------------------------------------------------------------
    # LM Studio model benchmarking
    # ------------------------------------------------------------------

    def benchmark_lmstudio(
        self,
        prompt: str = "Reply with EXACTLY one word describing your model.",
        max_tokens: int = 10,
        temperature: float = 0,
        lmstudio_url: str = "http://localhost:1234",
    ) -> dict[str, Any]:
        """
        Benchmark ALL models found in LM Studio.

        Connects to LM Studio's OpenAI-compatible API at the given URL
        (default: http://localhost:1234). Discovers available models via
        /v1/models endpoint, then benchmarks each in parallel.

        Also scans the filesystem (~/.lmstudio/models/) to list all
        GGUF model files available on disk, for reference when LM
        Studio is not currently running.

        All results recorded to DirectMemory.

        Args:
            prompt: Prompt to send to each model.
            max_tokens: Max tokens per response.
            temperature: Sampling temperature.
            lmstudio_url: LM Studio API base URL (default port 1234).

        Returns:
            dict with per-model results sorted by speed, aggregate stats,
            and filesystem model inventory.
        """
        start = time.time()

        # -- Scan filesystem for GGUF models --
        lmstudio_models_dir = Path.home() / ".lmstudio" / "models"
        disk_models: list[dict[str, Any]] = []
        if lmstudio_models_dir.exists():
            for gguf in sorted(lmstudio_models_dir.rglob("*.gguf")):
                size_mb = round(gguf.stat().st_size / (1024 * 1024), 1)
                disk_models.append({
                    "path": str(gguf.relative_to(lmstudio_models_dir)),
                    "size_mb": size_mb,
                })

        # -- Check if LM Studio server is running --
        api_base = lmstudio_url.rstrip("/")
        api_models_url = api_base + "/v1/models"
        server_running = False
        api_models: list[str] = []
        try:
            req = urllib.request.Request(api_models_url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            api_models = [m["id"] for m in data.get("data", []) if "id" in m]
            server_running = True
        except Exception as exc:
            note_failure("PARALLEL_INFERENCE_WORM:L520", exc)
            server_running = False

        self._record(
            "benchmark_lmstudio.start",
            {"prompt": prompt[:800], "server_running": server_running,
             "api_models": api_models, "disk_models": len(disk_models)},
            tags=["benchmark", "lmstudio"],
        )

        lmstudio_results: dict[str, Any] = {
            "strategy": "benchmark_lmstudio",
            "lmstudio_server_running": server_running,
            "api_base_url": api_base,
            "prompt": prompt[:200],
            "api_models": api_models,
            "disk_models": disk_models,
            "disk_model_count": len(disk_models),
            "total_disk_size_gb": round(
                sum(m["size_mb"] for m in disk_models) / 1024, 1
            ) if disk_models else 0,
            "elapsed_ms": 0,
            "results": [],
        }

        if not server_running:
            # LM Studio not running -- report available models on disk
            lmstudio_results["status"] = (
                "LM Studio server is NOT running on " + api_base + ". "
                "Start LM Studio and enable the local inference server "
                "(Settings > Local Inference Server) to benchmark models."
            )
            lmstudio_results["total_models_attempted"] = 0
            lmstudio_results["total_models_succeeded"] = 0
            lmstudio_results["total_models_failed"] = 0
            lmstudio_results["elapsed_ms"] = int((time.time() - start) * 1000)

            self._record(
                "benchmark_lmstudio.not_running",
                {"api_url": api_base, "disk_models": len(disk_models),
                 "total_disk_gb": lmstudio_results["total_disk_size_gb"]},
                severity="warning",
                tags=["benchmark", "lmstudio"],
            )
            return lmstudio_results

        # -- LM Studio is running -- benchmark all found models --
        if not api_models:
            # Try a default model name if API returned no models
            api_models = ["local-model"]

        results_list: list[dict[str, Any]] = []
        results_lock = threading.Lock()

        def worker(model_id: str) -> dict[str, Any]:
            t_start = time.time()
            try:
                payload = {
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    api_base + "/v1/chat/completions",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=600) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

                elapsed = int((time.time() - t_start) * 1000)
                choice = result.get("choices", [{}])[0]
                msg = choice.get("message", {})
                usage = result.get("usage", {})

                return {
                    "model": model_id,
                    "success": True,
                    "content": msg.get("content", ""),
                    "elapsed_ms": elapsed,
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                    "finish_reason": choice.get("finish_reason", ""),
                }
            except Exception as e:
                note_failure("PARALLEL_INFERENCE_WORM:L610", e)
                return {
                    "model": model_id,
                    "success": False,
                    "error": str(e)[:200],
                    "elapsed_ms": int((time.time() - t_start) * 1000),
                }

        with FreeThreadPool(
            max_workers=len(api_models), thread_name_prefix="bm-lmstudio"
        ) as pool:
            futures = {pool.submit(worker, m): m for m in api_models}
            for future in as_completed(futures):
                try:
                    r = future.result()
                    with results_lock:
                        results_list.append(r)
                except Exception as e:
                    note_failure("PARALLEL_INFERENCE_WORM:L627", e)
                    with results_lock:
                        results_list.append({
                            "model": futures[future],
                            "success": False,
                            "error": str(e)[:200],
                            "elapsed_ms": 0,
                        })

        # Sort by speed
        results_list.sort(key=lambda r: r.get("elapsed_ms", 0))
        success_results = [r for r in results_list if r["success"]]
        failure_results = [r for r in results_list if not r["success"]]

        elapsed_total = int((time.time() - start) * 1000)
        fastest = success_results[0] if success_results else None
        slowest = success_results[-1] if len(success_results) > 1 else None

        lmstudio_results.update({
            "total_models_attempted": len(api_models),
            "total_models_succeeded": len(success_results),
            "total_models_failed": len(failure_results),
            "elapsed_ms": elapsed_total,
            "fastest_model": fastest["model"] if fastest else None,
            "fastest_ms": fastest["elapsed_ms"] if fastest else None,
            "slowest_model": slowest["model"] if slowest else None,
            "slowest_ms": slowest["elapsed_ms"] if slowest else None,
            "results": results_list,
        })

        with self._lock:
            self._total_local_benchmarks += 1

        self._record(
            "benchmark_lmstudio.complete",
            {
                "attempted": len(api_models),
                "succeeded": len(success_results),
                "failed": len(failure_results),
                "elapsed_ms": elapsed_total,
                "fastest": lmstudio_results["fastest_model"],
                "disk_models": len(disk_models),
            },
            tags=["benchmark", "lmstudio"],
        )
        self._publish({
            "event": "benchmark_lmstudio.complete",
            "succeeded": len(success_results),
            "failed": len(failure_results),
        })

        return lmstudio_results

    # ------------------------------------------------------------------
    # Max inference (convenience)
    # ------------------------------------------------------------------

    def max_inference(
        self,
        prompt: str = "",
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
        system_prompt: (str | None) = None,
    ) -> dict[str, Any]:
        """MAXIMUM INFERENCE — all remote providers simultaneously."""
        return self.execute(prompt=prompt, strategy="max",
                           max_tokens=max_tokens, temperature=temperature,
                           system_prompt=system_prompt)

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _format_broadcast_result(self, b: BroadcastResult) -> dict[str, Any]:
        return {
            "success": b.success_count > 0,
            "strategy": "broadcast",
            "providers_attempted": b.total_providers_attempted,
            "providers_succeeded": b.success_count,
            "providers_failed": b.failure_count,
            "total_tokens": b.total_tokens,
            "elapsed_ms": b.elapsed_ms,
            "fastest": b.fastest.provider if b.fastest else None,
            "fastest_ms": b.fastest.elapsed_ms if b.fastest else None,
            "fastest_content": (b.fastest.content[:200] + "...") if b.fastest and len(b.fastest.content) > 200 else (b.fastest.content if b.fastest else ""),
            "stats": {
                "total_providers_available": b.total_providers_available,
                "total_tokens": b.total_tokens,
            },
            "results": [
                {
                    "provider": r.provider,
                    "success": r.success,
                    "elapsed_ms": r.elapsed_ms,
                    "content": r.content,
                    "error": r.error,
                }
                for r in b.results
            ],
        }

    def _format_ensemble_result(self, e: EnsembleResult) -> dict[str, Any]:
        return {
            "success": e.success_count > 0,
            "strategy": "ensemble",
            "consensus": e.consensus,
            "concatenated": e.concatenated,
            "best_result": e.best_result,
            "providers_succeeded": e.success_count,
            "providers_failed": e.failure_count,
            "total_tokens": e.total_tokens,
            "elapsed_ms": e.elapsed_ms,
            "results": [
                {
                    "provider": r.provider,
                    "success": r.success,
                    "elapsed_ms": r.elapsed_ms,
                    "content": r.content,
                    "error": r.error,
                }
                for r in e.results
            ],
        }

    def _format_pipeline_result(self, p: Any) -> dict[str, Any]:
        return {
            "success": all(r.success for r in p.step_results),
            "strategy": "pipeline",
            "final_output": p.final_output,
            "steps": len(p.steps),
            "total_tokens": p.total_tokens,
            "elapsed_ms": p.elapsed_ms,
            "results": [
                {
                    "step": i,
                    "provider": r.provider,
                    "success": r.success,
                    "elapsed_ms": r.elapsed_ms,
                    "content": r.content,
                    "error": r.error,
                }
                for i, r in enumerate(p.step_results)
            ],
        }

    def _format_max_result(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": raw.get("stats", {}).get("providers_succeeded", 0) > 0,
            "strategy": "max_inference",
            "consensus": raw.get("consensus", ""),
            "debate": raw.get("debate", ""),
            "fastest_provider": raw.get("fastest_provider"),
            "fastest_elapsed_ms": raw.get("fastest_elapsed_ms"),
            "providers_succeeded": raw.get("stats", {}).get("providers_succeeded", 0),
            "providers_failed": raw.get("stats", {}).get("providers_failed", 0),
            "total_tokens": raw.get("stats", {}).get("total_tokens", 0),
            "elapsed_ms": raw.get("elapsed_ms", 0),
            "all_results": raw.get("all_results", []),
        }

    # ------------------------------------------------------------------
    # Stats & introspection
    # ------------------------------------------------------------------

    def get_engine(self) -> ParallelInferenceEngine:
        """Get the underlying ParallelInferenceEngine."""
        return self._engine

    def list_all_providers(self) -> list[dict[str, Any]]:
        """list all registered providers with key status."""
        return self._engine.report()["providers"]

    def list_available_providers(self) -> list[dict[str, Any]]:
        """list all providers with valid keys and healthy circuits."""
        return self._engine.get_available_providers()

    def get_stats(self) -> dict[str, Any]:
        """Get worm statistics."""
        return {
            "version": self.VERSION,
            "exec_count": self._exec_count,
            "total_tokens": self._total_tokens,
            "total_local_benchmarks": self._total_local_benchmarks,
            "engine_summary": self._engine.summary,
        }

    def get_status(self) -> dict[str, Any]:
        """Status entry point for the ClusterWORMZ generic dispatcher.

        ClusterWORMZ._run_worm_method prefers get_status() over execute().
        Without this method, the dispatcher fell through to execute() with no
        prompt, producing a tight 'No prompt' error loop (and memory spam).

        Returns a non-empty dict regardless of state so the dispatcher
        classifies this worm as healthy without triggering an inference.
        """
        return {
            "success": True,
            "method": "get_status",
            "status": self.get_stats(),
        }

    def __repr__(self) -> str:
        return (f"ParallelInferenceWorm(v{self.VERSION}, "
                f"execs={self._exec_count}, tokens={self._total_tokens}, "
                f"benchmarks={self._total_local_benchmarks})")


# --- Standalone test --------------------------------------------------------

if __name__ == "__main__":
    print(f"ParallelInferenceWorm v{ParallelInferenceWorm.VERSION}")
    print("=" * 60)

    worm = ParallelInferenceWorm()

    # Show available providers
    print("\\nAvailable providers:")
    for p in worm.list_available_providers():
        print(f"  {p['id']:25s} {p['name']}")

    # Run a quick broadcast test
    print("\\nTesting broadcast ('Reply OK')...")
    result = worm.execute(prompt="Reply EXACTLY: OK", max_tokens=3, temperature=0)
    print(f"  Success: {result.get('success')}")
    print(f"  Succeeded: {result.get('providers_succeeded')} / {result.get('providers_attempted')}")
    print(f"  Fastest: {result.get('fastest')} ({result.get('fastest_ms')}ms)")
    if result.get("results"):
        for r in result["results"]:
            status = "OK" if r.get("success") else "FAIL"
            cp = (r.get("content", "")[:20] + "...") if len(r.get("content", "")) > 20 else r.get("content", "")
            print(f"    {r['provider']:20s} {status:4s} {r.get('elapsed_ms', 0):5d}ms  [{cp}]")

    print(f"\\nWorm stats: {worm.get_stats()}")
    print("\\nDone.")
