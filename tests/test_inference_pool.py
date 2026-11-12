"""
Tests for InferencePool — interface, error handling, and model dispatch.

Does NOT require a running Ollama server — tests infer() error paths
and validates the pool interface and dispatch logic.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from CORE.inference_pool import InferencePool, _max_concurrent_for_model, _ollama_infer


class TestInferencePoolBasics:
    def test_pool_creation(self):
        pool = InferencePool(max_concurrent=2)
        assert pool is not None
        stats = pool.stats
        assert stats["max_concurrent"] == 2
        assert "ollama_base" in stats
        assert "vram_gb" in stats

    def test_infer_no_server(self):
        """infer() should gracefully handle connection refused."""
        pool = InferencePool(max_concurrent=1)
        result = pool.infer("hello", model="test-model", timeout=1)
        assert result.get("success") is False
        assert "error" in result
        assert "Connection failed" in result["error"] or "HTTP" in result["error"] or "error" in result["error"]

    def test_infer_many_empty(self):
        pool = InferencePool(max_concurrent=2)
        results = pool.infer_many([])
        assert results == []

    def test_infer_many_error_handling(self):
        pool = InferencePool(max_concurrent=2)
        tasks = [
            {"prompt": "hi", "model": "test-model", "timeout": 1},
            {"prompt": "hello", "model": "test-model", "timeout": 1},
        ]
        results = pool.infer_many(tasks, timeout=2)
        # collect_results wraps each return in {success, result, error}
        # success=True means the function returned (no exception), 
        # the actual _ollama_infer result is in "result"
        assert len(results) == 2
        for r in results:
            # The function completed without exception (success=True at pool level)
            # But the actual inference result is in r["result"]
            assert isinstance(r.get("result"), dict), f"Expected dict result, got {r}"
            # _ollama_infer returns success=False for connection errors
            assert r["result"].get("success") is False,\
                f"Expected failure in _ollama_infer, got: {r['result']}"

    def test_classify_many(self):
        pool = InferencePool(max_concurrent=1)
        results = pool.classify_many(["a", "b"], model="test-model")
        assert len(results) == 2

    def test_max_concurrent_for_model(self):
        # llama3.2:1b takes ~0.6GB on 8GB with 80% headroom = 6.4GB usable -> 6.4/0.6 ≈ 10
        c = _max_concurrent_for_model("llama3.2:1b")
        assert 1 <= c <= 12, f"Expected 1-12, got {c}"

        # gemma3:4b takes ~3.2GB -> 6.4/3.2 = 2
        c = _max_concurrent_for_model("gemma3:4b")
        assert c <= 3, f"Expected <=3, got {c}"

        # Unknown model should use default (2.0GB)
        c = _max_concurrent_for_model("unknown-model")
        assert 1 <= c <= 5, f"Expected 1-5, got {c}"


class TestInferencePoolBenchmark:
    def test_benchmark_structure(self):
        pool = InferencePool(max_concurrent=1)
        result = pool.benchmark(model="test-model", iterations=1)
        assert "model" in result
        assert result["model"] == "test-model"
        assert result["iterations"] == 1
        assert result["total"] == 1
        # benchmark unwraps collect_results and checks _ollama_infer success
        assert "successful" in result
        assert "failed" in result
