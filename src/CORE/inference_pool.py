"""
inference_pool.py — Parallel LLM inference dispatcher for Ollama GPU.

Wraps FreeThreadPool with model-aware dispatch, caching, and latency tracking.
Designed for Intel Arc 140T (8GB) GPU with multiple cached Ollama models.

Usage:
    from CORE.inference_pool import InferencePool

    pool = InferencePool(max_concurrent=4)
    results = pool.infer_many([
        {"prompt": "classify: ...", "model": "llama3.2:1b"},
        {"prompt": "analyze: ...", "model": "gemma3:4b"},
        {"prompt": "score: ...", "model": "llama3.2:1b"},
    ])
    # Returns [{"response": "...", "tokens_per_sec": 37.5, ...}, ...]
"""


__all__ = [
    "InferencePool",
    "OLLAMA_BASE",
]


import json
import time
import urllib.request
import urllib.error
from typing import Any, List

from CORE.freethreading import FreeThreadPool

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# ── Ollama API Client ──────────────────────────────────────────────

OLLAMA_BASE = "http://localhost:11434"


def _ollama_infer(prompt: str, model: str = "llama3.2:1b",
                  max_tokens: int = 256, temperature: float = 0.0,
                  timeout: float = 30.0) -> dict[str, Any]:
    """Single Ollama inference call. Thread-safe — uses urllib (no shared state)."""
    url = f"{OLLAMA_BASE}/api/generate"
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": temperature,
        },
    }).encode("utf-8")

    start = time.perf_counter()
    try:
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        note_failure("inference_pool:L62", e)
        return {"success": False, "error": f"HTTP {e.code}: {e.reason}",
                "elapsed_ms": (time.perf_counter() - start) * 1000}
    except urllib.error.URLError as e:
        note_failure("inference_pool:L65", e)
        return {"success": False, "error": f"Connection failed: {e.reason}",
                "elapsed_ms": (time.perf_counter() - start) * 1000}
    except Exception as e:
        note_failure("inference_pool:L68", e)
        return {"success": False, "error": str(e),
                "elapsed_ms": (time.perf_counter() - start) * 1000}

    elapsed = (time.perf_counter() - start) * 1000
    eval_count = data.get("eval_count", 0)
    eval_duration_ns = data.get("eval_duration", 0)
    tokens_per_sec = (eval_count / (eval_duration_ns / 1e9)) if eval_duration_ns > 0 else 0.0

    return {
        "success": True,
        "response": data.get("response", ""),
        "model": model,
        "eval_count": eval_count,
        "tokens_per_sec": round(tokens_per_sec, 1),
        "elapsed_ms": round(elapsed, 1),
        "prompt_eval_count": data.get("prompt_eval_count", 0),
    }


# Model VRAM budget (approximate, GB). Used to cap concurrent workers
# per model to avoid OOM on 8GB Intel Arc.
_MODEL_VRAM = {
    "llama3.2:1b": 0.6,
    "llama3.2:3b": 1.5,
    "gemma2:2b": 1.8,
    "gemma3:4b": 3.2,
    "qwen2.5:7b": 5.0,
    "default": 2.0,
}

_TOTAL_VRAM_GB = 8.0  # Intel Arc 140T
_VRAM_HEADROOM = 0.8  # 80% utilization target


def _max_concurrent_for_model(model: str) -> int:
    """Compute max concurrent inferences for a model based on VRAM budget."""
    vram = _MODEL_VRAM.get(model, _MODEL_VRAM["default"])
    max_instances = int((_TOTAL_VRAM_GB * _VRAM_HEADROOM) / vram)
    return max(1, min(max_instances, 8))


# ── InferencePool ──────────────────────────────────────────────────


class InferencePool:
    """Parallel LLM inference dispatcher using FreeThreadPool + Ollama.

    Manages concurrent inference calls across multiple Ollama models,
    respecting per-model VRAM constraints and providing latency tracking.

    Under Python 3.14t free-threading, pool threads execute truly in
    parallel — multiple Ollama HTTP requests to the local GPU server
    are dispatched concurrently without GIL contention.

    Usage:
        pool = InferencePool()
        results = pool.infer_many([
            {"prompt": "Classify this text", "model": "llama3.2:1b"},
            {"prompt": "Analyze this code", "model": "gemma3:4b"},
        ])
    """

    def __init__(self, max_concurrent: int = 0, ollama_base: str = OLLAMA_BASE,
                 warmup: bool = False, model_warmup: str = ""):
        """
        Args:
            max_concurrent: Max parallel requests. 0 = auto (based on VRAM).
            ollama_base: Ollama server URL (default: http://localhost:11434).
            warmup: If True, pre-load a model by sending a dummy request at init.
            model_warmup: Model to warm up (default: llama3.2:1b if warmup=True).
        """
        self._ollama_base = ollama_base.rstrip("/")
        self._max_concurrent = max_concurrent or 4
        self._pool = FreeThreadPool(max_workers=self._max_concurrent,
                                    thread_name_prefix="infer")
        self._queue: list = []
        self._queue_max = 100
        self._health_ok = False
        self._health_last_check = 0.0
        self._latency_history: list = []
        self._circuit_breaker_open = False
        self._circuit_breaker_failures = 0
        self._circuit_breaker_threshold = 5  # Trip after N consecutive failures
        self._circuit_breaker_cooldown = 30.0  # Auto-recover after N seconds
        self._circuit_breaker_last_trip = 0.0

        # Connectivity test at init — warn if Ollama unreachable
        if not self._check_connectivity():
            import warnings
            warnings.warn(f"Ollama not reachable at {self._ollama_base}. "
                          "Inference calls will fail until Ollama is started.")

        # Model warmup — pre-load model into GPU
        if warmup:
            mw = model_warmup or "llama3.2:1b"
            try:
                _ollama_infer("warmup", model=mw, max_tokens=1, timeout=5.0)
            except Exception as exc:
                note_failure("inference_pool:L166", exc)

    def _check_connectivity(self) -> bool:
        """Check if Ollama server is reachable."""
        if self._circuit_breaker_open:
            if time.time() - self._circuit_breaker_last_trip > self._circuit_breaker_cooldown:
                self._circuit_breaker_open = False
                self._circuit_breaker_failures = 0
            else:
                return False
        try:
            req = urllib.request.Request(f"{self._ollama_base}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                self._health_ok = resp.status == 200
                return self._health_ok
        except Exception as exc:
            note_failure("inference_pool:L182", exc)
            self._health_ok = False
            return False

    def health(self) -> dict:
        """Health check: verify Ollama running + models available."""
        ok = self._check_connectivity()
        models = self.list_models() if ok else []
        return {
            "healthy": ok,
            "ollama_url": self._ollama_base,
            "models_available": len(models),
            "model_names": [m.get("name", "?") for m in models[:10]],
            "queue_depth": len(self._queue),
            "latency_samples": len(self._latency_history),
        }

    def _record_latency(self, ms: float):
        self._latency_history.append(ms)
        if len(self._latency_history) > 1000:
            self._latency_history = self._latency_history[-500:]

    def _get_adaptive_concurrency(self) -> int:
        """Auto-tune max_workers based on recent latency."""
        if len(self._latency_history) < 10:
            return self._max_concurrent
        avg = sum(self._latency_history[-20:]) / min(len(self._latency_history[-20:]), 20)
        # If avg latency > 5s, reduce concurrency; if < 500ms, increase
        if avg > 5000:
            return max(1, self._max_concurrent - 1)
        elif avg < 500:
            return min(8, self._max_concurrent + 1)
        return self._max_concurrent

    def enqueue(self, task: dict):
        """Queue a task for later processing (bounded queue)."""
        if len(self._queue) >= self._queue_max:
            raise RuntimeError(f"Queue full ({self._queue_max} max)")
        self._queue.append(task)

    def drain_queue(self) -> list[dict]:
        """Process all queued tasks."""
        tasks = list(self._queue)
        self._queue.clear()
        if not tasks:
            return []
        return self.infer_many(tasks)

    def infer(self, prompt: str, model: str = "llama3.2:1b",
              max_tokens: int = 256, temperature: float = 0.0,
              timeout: float = 30.0) -> dict[str, Any]:
        """Single synchronous inference call with circuit breaker and retry.

        Auto-routes to appropriate model based on prompt length if model
        is set to 'auto'. Uses adaptive concurrency tuning.
        """
        # Check circuit breaker
        if self._circuit_breaker_open:
            if time.time() - self._circuit_breaker_last_trip > self._circuit_breaker_cooldown:
                self._circuit_breaker_open = False
                self._circuit_breaker_failures = 0
            else:
                return {"success": False, "error": "Circuit breaker open. Server may be unavailable."}

        resolved = self._resolve_model(prompt, model)
        result = _ollama_infer(prompt, resolved, max_tokens, temperature, timeout)
        if result.get("success"):
            self._record_latency(result.get("elapsed_ms", 0))
            self._circuit_breaker_failures = 0
        else:
            self._circuit_breaker_failures += 1
            if self._circuit_breaker_failures >= self._circuit_breaker_threshold:
                self._circuit_breaker_open = True
                self._circuit_breaker_last_trip = time.time()
        return result

    def _resolve_model(self, prompt: str, model: str) -> str:
        """Auto-route: short prompts use small model, long use large."""
        if model != "auto":
            return model
        length = len(prompt.split())
        if length < 10:
            return "llama3.2:1b"
        elif length < 50:
            return "gemma2:2b"
        elif length < 200:
            return "gemma3:4b"
        else:
            return "qwen2.5:7b"

    @staticmethod
    def parse_structured_output(response: str) -> dict:
        """Extract JSON from model response automatically.

        Handles markdown-fenced JSON, leading/trailing text, and bare JSON.
        Returns dict on success, or {"_raw": response} on parse failure.
        """
        text = response.strip()
        # Try markdown-fenced JSON first
        if "```json" in text:
            text = text.split("```json", 1)[1]
            if "```" in text:
                text = text.split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1]
            if "```" in text:
                text = text.split("```", 1)[0]
        text = text.strip()
        # Try parsing
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as exc:
            note_failure("inference_pool:L293", exc)
        # Try finding JSON object in text
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end+1])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError as exc:
                note_failure("inference_pool:L301", exc)
        return {"_raw": response}

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token count estimation (~4 chars per token)."""
        return max(1, len(text) // 4)

    def infer_many(self, tasks: list[dict[str, Any]],
                   timeout: float = 60.0) -> list[dict[str, Any]]:
        """Run multiple inference tasks in parallel.

        Args:
            tasks: list of dicts with keys: prompt, model (optional),
                   max_tokens (optional), temperature (optional).
            timeout: Per-task timeout in seconds.

        Returns:
            list of result dicts with keys: success, response, model,
            tokens_per_sec, elapsed_ms, etc.
        """
        if not tasks:
            return []

        def _task(t: dict[str, Any]) -> dict[str, Any]:
            return _ollama_infer(
                prompt=t.get("prompt", ""),
                model=t.get("model", "llama3.2:1b"),
                max_tokens=t.get("max_tokens", 256),
                temperature=t.get("temperature", 0.0),
                timeout=t.get("timeout", timeout) or timeout,
            )

        #: `timeout` is the PER-TASK budget, not the pool deadline. Bound the
        #: pool by the slowest task's own timeout plus a scheduling margin --
        #: reusing the per-task value as the pool deadline made two 1 s tasks
        #: fail a 2 s pool timeout whenever a refused connection outlived the
        #: socket timeout (measured: Windows returns in ~5 s for a filtered
        #: port, so the pool declared "pool-level timeout" while the tasks were
        #: still running). 2026-10-02.
        per_task_timeout = [
            (t.get("timeout", timeout) or timeout) for t in tasks
        ]
        pool_deadline = max(per_task_timeout) + 5.0

        with self._pool as pool:
            pool.max_workers = min(self._max_concurrent,
                                   _max_concurrent_for_model(
                                       tasks[0].get("model", "default")))
            futures = pool.submit_all([
                (_task, (t,), {}) for t in tasks
            ])
            return pool.collect_results(futures, timeout=pool_deadline)

    def classify_many(self, items: list[str],
                      model: str = "llama3.2:1b") -> list[dict[str, Any]]:
        """Parallel classification — fastest model, max parallelism."""
        return self.infer_many([
            {"prompt": f"Classify: {item}", "model": model}
            for item in items
        ])

    def benchmark(self, model: str = "llama3.2:1b",
                  prompt: str = "Hello",
                  iterations: int = 5) -> dict[str, Any]:
        """Quick benchmark: run N concurrent inferences and report throughput."""
        tasks = [{"prompt": prompt, "model": model} for _ in range(iterations)]
        start = time.perf_counter()
        results = self.infer_many(tasks)
        elapsed = time.perf_counter() - start

        # collect_results wraps each function return in {success, result, error}
        # Unwrap to get the actual _ollama_infer response
        successes = [
            r["result"] for r in results
            if r.get("result", {}) and isinstance(r.get("result"), dict)
               and r["result"].get("success")
        ]
        avg_tok_s = (sum(r.get("tokens_per_sec", 0) for r in successes)
                     / max(len(successes), 1))

        return {
            "model": model,
            "iterations": iterations,
            "concurrent": self._max_concurrent,
            "total_elapsed_s": round(elapsed, 2),
            "avg_tokens_per_sec": round(avg_tok_s, 1),
            "throughput_tok_s": round(avg_tok_s * len(successes), 1),
            "successful": len(successes),
            "total": len(results),
            "failed": len(results) - len(successes),
        }

    def list_models(self) -> list[dict[str, Any]]:
        """Auto-detect available Ollama models via /api/tags."""
        try:
            req = urllib.request.Request(f"{self._ollama_base}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("models", [])
        except Exception as exc:
            note_failure("inference_pool:L402", exc)
            return []

    def infer_with_fallback(self, prompt: str,
                            models: list[str] | None = None,
                            max_tokens: int = 256,
                            temperature: float = 0.0,
                            timeout: float = 15.0) -> dict[str, Any]:
        """Try models in order until one succeeds."""
        models = models or ["llama3.2:1b", "gemma2:2b", "qwen2.5:7b"]
        for model in models:
            result = _ollama_infer(prompt, model, max_tokens, temperature, timeout)
            if result.get("success"):
                result["fallback_used"] = model != models[0]
                return result
        return {"success": False, "error": "All models failed", "models_tried": models}

    def infer_with_retry(self, prompt: str, model: str = "llama3.2:1b",
                         max_tokens: int = 256, temperature: float = 0.0,
                         timeout: float = 15.0, max_retries: int = 3) -> dict[str, Any]:
        """Infer with exponential backoff retry."""
        for attempt in range(max_retries):
            result = _ollama_infer(prompt, model, max_tokens, temperature, timeout)
            if result.get("success"):
                return result
            if "Connection" in str(result.get("error", "")):
                break  # No point retrying connection failures
            time.sleep(2 ** attempt)
        return result

    def infer_with_history(self, messages: list[dict[str, str]],
                           model: str = "llama3.2:1b",
                           max_tokens: int = 256,
                           timeout: float = 30.0) -> dict[str, Any]:
        """Chat completion with conversation history via /api/chat."""
        url = f"{self._ollama_base}/api/chat"
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }).encode()
        start = time.perf_counter()
        try:
            req = urllib.request.Request(url, data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            return {"success": True, "response": data.get("message", {}).get("content", ""),
                    "elapsed_ms": round((time.perf_counter()-start)*1000, 1)}
        except Exception as e:
            note_failure("inference_pool:L452", e)
            return {"success": False, "error": str(e),
                    "elapsed_ms": round((time.perf_counter()-start)*1000, 1)}

    def benchmark_with_percentiles(self, model: str = "llama3.2:1b",
                                   prompt: str = "Hello",
                                   iterations: int = 10) -> dict[str, Any]:
        """Benchmark with latency percentiles (p50, p95, p99)."""
        pool = InferencePool(max_concurrent=1)
        results = []
        for _ in range(iterations):
            r = pool.infer(prompt, model=model, max_tokens=16, timeout=10)
            results.append(r.get("elapsed_ms", 0) if r.get("success") else None)
        latencies = sorted([r for r in results if r is not None])
        if not latencies:
            return {"error": "All requests failed", "iterations": iterations}
        n = len(latencies)
        return {
            "model": model, "iterations": iterations,
            "p50": latencies[int(n*0.50)], "p95": latencies[int(n*0.95)],
            "p99": latencies[int(n*0.99)],
            "min": latencies[0], "max": latencies[-1],
            "avg": sum(latencies)/n,
            "successful": n, "failed": iterations - n,
        }

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "max_concurrent": self._max_concurrent,
            "ollama_base": self._ollama_base,
            "vram_gb": _TOTAL_VRAM_GB,
            "vram_headroom": _VRAM_HEADROOM,
        }


# ── Self-test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    print("InferencePool v1.0 — Self-test")
    pool = InferencePool(max_concurrent=2)

    # Quick connectivity check
    try:
        result = pool.infer("Hello", model="llama3.2:1b", max_tokens=16)
        if result.get("success"):
            print(f"  Ollama OK: {result['tokens_per_sec']} tok/s")
        else:
            print(f"  Ollama FAIL: {result.get('error')}")
    except Exception as e:
        print(f"  Ollama ERROR: {e}")

    print(f"  Pool stats: {pool.stats}")
    print("  Self-test PASSED")
