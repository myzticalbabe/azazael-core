#!/usr/bin/env python3
"""Deeper tests for Eve InferenceEngine."""
import sys
import asyncio
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import numpy as np
import pytest

# Skip torch tests if not available
try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


def test_engine_init_no_cache():
    from eve_unleashed.engine import InferenceEngine
    from eve_unleashed.config import EveConfig
    cfg = EveConfig(enable_cache=False)
    e = InferenceEngine(cfg)
    assert e._cache is None
    assert e.cache_stats == {"enabled": False}
    e.shutdown()


def test_engine_stats_empty():
    from eve_unleashed.engine import InferenceEngine
    e = InferenceEngine()
    st = e.stats
    assert st["inference_count"] == 0
    assert st["loaded_models"] == 0
    e.shutdown()


def test_engine_list_models_empty():
    from eve_unleashed.engine import InferenceEngine
    e = InferenceEngine()
    assert e.list_models() == []
    e.shutdown()


def test_engine_infer_keyerror():
    from eve_unleashed.engine import InferenceEngine
    e = InferenceEngine()
    with pytest.raises(KeyError, match="Model not loaded"):
        e.infer("nope:handle", [1.0])
    e.shutdown()


def test_engine_unload_model():
    from eve_unleashed.engine import InferenceEngine
    e = InferenceEngine()
    e.loaded_models["torch:x"] = object()
    e.unload_model("torch:x")
    assert "torch:x" not in e.loaded_models
    e.shutdown()


def test_engine_cache_invalidate():
    from eve_unleashed.engine import InferenceEngine
    from eve_unleashed.config import EveConfig
    e = InferenceEngine(EveConfig(enable_cache=True))
    # no cache entries; should not raise
    e.cache_invalidate()
    e.cache_invalidate("h")
    e.shutdown()


def test_engine_batch_infer():
    from eve_unleashed.engine import InferenceEngine
    from unittest.mock import MagicMock
    e = InferenceEngine()
    # fake model + loader
    e.loaded_models["sklearn:m"] = MagicMock()
    e.loaded_models["sklearn:m"].predict = lambda x: np.array([1.0])
    res = e.batch_infer("sklearn:m", [[1.0, 2.0], [3.0, 4.0]])
    assert len(res.results) == 2
    e.shutdown()


def test_engine_warmup_zero():
    from eve_unleashed.engine import InferenceEngine
    from eve_unleashed.config import EveConfig
    cfg = EveConfig(warmup_iterations=0)
    e = InferenceEngine(cfg)
    # _warmup_model should return early
    e._warmup_model = lambda h: None
    e.shutdown()


def test_engine_gpu_info():
    from eve_unleashed.engine import InferenceEngine
    e = InferenceEngine()
    gi = e.gpu_info
    assert "recommended_device" in gi
    e.shutdown()


def test_engine_create_engine():
    from eve_unleashed.engine import create_engine
    e = create_engine()
    assert e is not None
    e.shutdown()


def test_engine_infer_async():
    from eve_unleashed.engine import InferenceEngine
    e = InferenceEngine()
    e.loaded_models["sklearn:m"] = type("M", (), {"predict": lambda self, x: np.array([1.0])})()
    async def main():
        return await e.infer_async("sklearn:m", [1.0, 2.0])
    # Asserting the RESULT, not the absence of an exception: a swallowed error
    # here would pass while ``infer_async`` never delivered anything.
    result = asyncio.run(main())
    assert result.framework == "sklearn"
    assert result.predictions == [1.0]
    e.shutdown()


def test_engine_infer_stream_keyerror():
    from eve_unleashed.engine import InferenceEngine
    e = InferenceEngine()
    async def main():
        async for _ in e.infer_stream("nope:h", [1.0]):
            pass
    with pytest.raises(KeyError, match="Model not loaded"):
        asyncio.run(main())
    e.shutdown()


def test_engine_single_infer_sklearn():
    from eve_unleashed.engine import InferenceEngine
    from unittest.mock import MagicMock
    e = InferenceEngine()
    m = MagicMock()
    m.predict = lambda x: np.array([42.0])
    res = e._single_infer(m, "sklearn", [1.0, 2.0])
    assert res.predictions == [42.0]
    e.shutdown()


def test_engine_single_infer_gguf():
    from eve_unleashed.engine import InferenceEngine
    m = lambda text, max_tokens=128, echo=False: {"choices": [{"text": "hi"}]}
    e = InferenceEngine()
    res = e._single_infer(m, "gguf", "hello")
    assert res.predictions == ["hi"]
    e.shutdown()


@pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
def test_engine_single_infer_torch():
    from eve_unleashed.engine import InferenceEngine
    import torch
    import torch.nn as nn

    class Tiny(nn.Module):
        def forward(self, x):
            return x

    m = Tiny()
    e = InferenceEngine()
    res = e._single_infer(m, "torch", [1.0, 2.0, 3.0])
    assert res.framework == "torch"
    e.shutdown()


def test_engine_single_infer_unknown():
    from eve_unleashed.engine import InferenceEngine
    e = InferenceEngine()
    with pytest.raises(ValueError):
        e._single_infer(None, "nope", [1.0])
    e.shutdown()
