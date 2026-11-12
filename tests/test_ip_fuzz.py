import pytest
"""Fuzz tests for InferencePool — random model names, timeouts, max_tokens combos."""

import sys
import random
import string

sys.path.insert(0, "src")

from CORE.inference_pool import _ollama_infer, _max_concurrent_for_model


def test_random_model_names():
    """Fuzz: random model names should not crash _ollama_infer."""
    for _ in range(20):
        length = random.randint(3, 30)
        name = "".join(random.choices(string.ascii_lowercase + string.digits + "/:.", k=length))
        result = _ollama_infer("test", model=name, max_tokens=1, timeout=1)
        assert "success" in result
        assert "error" in result or result.get("success") is not None


def test_extreme_max_tokens():
    """Fuzz: extreme max_tokens values should not crash."""
    for val in [0, -1, 1, 100000, 999999999]:
        result = _ollama_infer("hello", model="llama3.2:1b", max_tokens=val, timeout=1)
        assert "success" in result


def test_empty_prompt():
    """Fuzz: empty prompt should not crash."""
    result = _ollama_infer("", model="llama3.2:1b", timeout=1)
    assert "success" in result


def test_very_long_prompt():
    """Fuzz: 100K char prompt should not crash."""
    result = _ollama_infer("x" * 100000, model="llama3.2:1b", max_tokens=1, timeout=1)
    assert "success" in result


def test_extreme_temperature():
    """Fuzz: extreme temperature values."""
    for temp in [-10.0, -1.0, 0.0, 1.0, 10.0, 100.0]:
        result = _ollama_infer("test", model="llama3.2:1b", temperature=temp, timeout=1)
        assert "success" in result


def test_zero_timeout():
    """Fuzz: zero timeout should not crash."""
    result = _ollama_infer("test", model="llama3.2:1b", timeout=0)
    assert "success" in result


def test_max_concurrent_for_model_edge_cases():
    """Fuzz: _max_concurrent_for_model with edge cases."""
    for model in ["", None, "unknown_model_xyz", "llama3.2:1b", "qwen2.5:7b"]:
        if model is None:
            continue
        result = _max_concurrent_for_model(model)
        assert isinstance(result, int)
        assert 1 <= result <= 16


def test_parse_structured_output_fuzz():
    """Fuzz: parse_structured_output with random strings."""
    from CORE.inference_pool import InferencePool

    for _ in range(50):
        length = random.randint(0, 200)
        text = "".join(random.choices(string.printable, k=length))
        result = InferencePool.parse_structured_output(text)
        assert isinstance(result, dict)


def test_estimate_tokens_fuzz():
    """Fuzz: estimate_tokens with random strings."""
    from CORE.inference_pool import InferencePool

    for _ in range(50):
        length = random.randint(0, 10000)
        text = "".join(random.choices(string.ascii_letters + " ", k=length))
        count = InferencePool.estimate_tokens(text)
        assert isinstance(count, int)
        assert count >= 1
        assert count <= len(text) + 1


def test_list_models_fuzz():
    """Fuzz: list_models handles unexpected return formats."""
    from CORE.inference_pool import InferencePool
    pool = InferencePool()
    models = pool.list_models()
    assert isinstance(models, list)
