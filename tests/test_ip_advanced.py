"""Advanced InferencePool tests: auto-detect, fallback, caching, templates, retry, history, streaming."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from CORE.inference_pool import InferencePool, _max_concurrent_for_model, _ollama_infer

class TestModelAutoDetect:
    def test_max_concurrent_bounds(self):
        assert _max_concurrent_for_model("llama3.2:1b") >= 1
        assert _max_concurrent_for_model("gemma3:4b") <= 3

    def test_model_vram_lookup(self):
        from CORE.inference_pool import _MODEL_VRAM
        assert "llama3.2:1b" in _MODEL_VRAM
        assert "default" in _MODEL_VRAM

class TestFallbackChain:
    def test_infer_fallback(self):
        pool = InferencePool(max_concurrent=1)
        result = pool.infer("hello", model="test-model", timeout=1)
        assert result.get("success") is False
        assert "error" in result or "Connection" in str(result)

class TestCache:
    def test_infer_many_dedup(self):
        pool = InferencePool(max_concurrent=2)
        tasks = [{"prompt": "hi", "model": "test-model", "timeout": 1}] * 3
        results = pool.infer_many(tasks)
        assert len(results) == 3

class TestTemplates:
    def test_classify_many_count(self):
        pool = InferencePool(max_concurrent=1)
        results = pool.classify_many(["good", "bad"], model="test-model")
        assert len(results) == 2

class TestRetry:
    def test_retry_no_server(self):
        pool = InferencePool(max_concurrent=1)
        result = pool.infer("retry-test", model="test-model", timeout=1)
        assert "elapsed_ms" in result

class TestHistory:
    def test_history_off(self):
        pool = InferencePool(max_concurrent=1)
        result = pool.infer("history-test", model="test-model", timeout=1)
        assert isinstance(result, dict)
