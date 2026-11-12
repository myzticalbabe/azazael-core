#!/usr/bin/env python3
"""
Eve Unleashed Inference Engine — Unit Tests
============================================
Tests for core.py, engine.py, config.py, and utils.py
matched to the actual API.
"""

import sys
import json
import time
import tempfile
import numpy as np
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_config_default():
    """Test default EveConfig values."""
    from eve_unleashed.config import EveConfig
    cfg = EveConfig()
    # "auto" is the default and a supported value: InferenceEngine.__init__
    # resolves it to a concrete device (cpu/cuda/xpu/mps) at construction.
    assert cfg.device == "auto"
    assert cfg.batch_size == 32
    assert cfg.precision == "fp32"
    assert cfg.enable_cache is True
    assert cfg.warmup_iterations == 3
    pass  # removed return dict

def test_config_gpu_optimized():
    """Test GPU-optimized preset."""
    from eve_unleashed.config import EveConfig
    cfg = EveConfig.gpu_optimized()
    assert cfg.device == "cuda"
    assert cfg.batch_size == 64
    assert cfg.precision == "fp16"
    assert cfg.num_workers == 4
    pass  # removed return dict

def test_config_cpu_optimized():
    """Test CPU-optimized preset."""
    from eve_unleashed.config import EveConfig
    cfg = EveConfig.cpu_optimized()
    assert cfg.device == "cpu"
    assert cfg.batch_size == 16
    assert cfg.precision == "int8"
    pass  # removed return dict

def test_config_serialization():
    """Test config to/from JSON file."""
    from eve_unleashed.config import EveConfig
    cfg = EveConfig(batch_size=64, precision="fp16")
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        cfg.to_file(f.name)
        path = f.name

    try:
        loaded = EveConfig.from_file(path)
        assert loaded.batch_size == 64
        assert loaded.precision == "fp16"
    finally:
        Path(path).unlink()
    pass  # removed return dict

def test_model_loader_detect_frameworks():
    """Test ModelLoader.detect_frameworks() returns dict."""
    from eve_unleashed.core import ModelLoader
    fw = ModelLoader.detect_frameworks()
    assert isinstance(fw, dict)
    assert "torch" in fw
    assert "sklearn" in fw
    assert "transformers" in fw
    # At least numpy should always be available since we import it
    pass  # removed return dict


def test_model_loader_guess_framework():
    """Test _guess_framework from file extensions."""
    from eve_unleashed.core import ModelLoader
    from pathlib import Path
    assert ModelLoader._guess_framework(Path("model.pt")) == "torch"
    assert ModelLoader._guess_framework(Path("model.pth")) == "torch"
    assert ModelLoader._guess_framework(Path("model.joblib")) == "sklearn"
    assert ModelLoader._guess_framework(Path("model.pkl")) == "sklearn"
    assert ModelLoader._guess_framework(Path("model.onnx")) == "onnx"
    assert ModelLoader._guess_framework(Path("model.h5")) == "tensorflow"
    assert ModelLoader._guess_framework(Path("model.xyz")) == "unknown"
    pass  # removed return dict

def test_inference_result():
    """Test InferenceResult dataclass fields."""
    from eve_unleashed.core import InferenceResult
    result = InferenceResult(
        model_name="test_model",
        framework="torch",
        input_shape=(1, 256),
        output_shape=(1, 10),
        latency_ms=12.5,
        predictions=[0.1, 0.2, 0.7],
        probabilities=[0.1, 0.2, 0.7],
    )
    assert result.model_name == "test_model"
    assert result.framework == "torch"
    assert result.latency_ms == 12.5
    assert result.input_shape == (1, 256)
    assert result.output_shape == (1, 10)
    assert result.predictions == [0.1, 0.2, 0.7]
    pass  # removed return dict

def test_engine_init():
    """Test InferenceEngine initialization."""
    from eve_unleashed.config import EveConfig
    from eve_unleashed.engine import InferenceEngine
    cfg = EveConfig(batch_size=16)
    engine = InferenceEngine(cfg)
    assert engine.config.batch_size == 16
    assert isinstance(engine.loaded_models, dict)
    assert len(engine.loaded_models) == 0
    assert isinstance(engine.available_frameworks, list)
    pass  # removed return dict


def test_engine_list_models():
    """Test list_models() returns empty initially."""
    from eve_unleashed.config import EveConfig
    from eve_unleashed.engine import InferenceEngine
    engine = InferenceEngine(EveConfig())
    assert engine.list_models() == []
    pass  # removed return dict

def test_engine_load_bad_framework():
    """Test loading with unsupported framework raises ValueError."""
    from eve_unleashed.config import EveConfig
    from eve_unleashed.engine import InferenceEngine
    engine = InferenceEngine(EveConfig())
    try:
        engine.load_model("fake.pt", "nonexistent")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass  # expected
    pass  # removed return dict

def test_engine_load_missing_model():
    """A usable framework plus a missing path is still FileNotFoundError.

    Pins the other half of the error contract: validating the framework before
    the path must not have been achieved by dropping the path check.
    """
    from eve_unleashed.config import EveConfig
    from eve_unleashed.core import ModelLoader
    from eve_unleashed.engine import InferenceEngine
    engine = InferenceEngine(EveConfig())
    missing = "definitely-missing-model.pt"

    avail = engine.available_frameworks
    if not avail:
        import pytest
        pytest.skip("no ML framework installed to name explicitly")

    # (a) explicitly named, installed framework + missing path
    try:
        engine.load_model(missing, avail[0])
        assert False, "Should have raised FileNotFoundError"
    except FileNotFoundError:
        pass  # expected

    # (b) "auto" resolved from a guessable extension + missing path
    if ModelLoader.guess_framework(Path(missing)) in avail:
        try:
            engine.load_model(missing)
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            pass  # expected
    pass  # removed return dict

def test_utils_prepare_input_array():
    """Test prepare_input_tensor with numpy array."""
    from eve_unleashed.utils import prepare_input_tensor
    data = np.random.randn(32, 256).astype(np.float32)
    result = prepare_input_tensor(data)
    assert result.shape == (32, 256)
    assert result.dtype == np.float32
    pass  # removed return dict

def test_utils_prepare_input_list():
    """Test prepare_input_tensor with Python list."""
    from eve_unleashed.utils import prepare_input_tensor
    data = [[1.0, 2.0], [3.0, 4.0]]
    result = prepare_input_tensor(data)
    assert result.shape == (2, 2)
    assert result.dtype == np.float32
    pass  # removed return dict

def test_utils_prepare_input_scalar():
    """Test prepare_input_tensor with scalar input."""
    from eve_unleashed.utils import prepare_input_tensor
    result = prepare_input_tensor(42)
    assert result.shape == (1, 1)
    assert result.dtype == np.float32
    pass  # removed return dict

def test_utils_postprocess_torch():
    """Test postprocess_output with torch framework."""
    from eve_unleashed.utils import postprocess_output
    pytest.importorskip(
        "torch",
        reason="torch in site-packages is a GIL-ABI wheel; a free-threaded "
               "interpreter loads only `t`-suffixed extensions, so the "
               "C-extension cannot import here",
    )
    import torch
    t = torch.tensor([[1.0, 2.0, 3.0]])
    result = postprocess_output(t, "torch")
    assert isinstance(result, list)
    assert result == [[1.0, 2.0, 3.0]]
    pass  # removed return dict

def test_utils_postprocess_sklearn():
    """Test postprocess_output with sklearn framework."""
    from eve_unleashed.utils import postprocess_output
    arr = np.array([0, 1, 0])
    result = postprocess_output(arr, "sklearn")
    assert result == [0, 1, 0]
    pass  # removed return dict

def test_utils_validate_shape_exact():
    """Test validate_input_shape with exact match."""
    from eve_unleashed.utils import validate_input_shape
    arr = np.ones((1, 256), dtype=np.float32)
    assert validate_input_shape(arr, (1, 256)) is True
    pass  # removed return dict

def test_utils_validate_shape_batch():
    """Test validate_input_shape with batched input."""
    from eve_unleashed.utils import validate_input_shape
    arr = np.ones((32, 3, 224, 224), dtype=np.float32)
    assert validate_input_shape(arr, (3, 224, 224), allow_batch=True) is True
    pass  # removed return dict

def test_utils_validate_shape_mismatch():
    """Test validate_input_shape with shape mismatch."""
    from eve_unleashed.utils import validate_input_shape
    arr = np.ones((1, 128), dtype=np.float32)
    assert validate_input_shape(arr, (1, 256)) is False
    pass  # removed return dict

def test_utils_softmax():
    """Test softmax computation."""
    from eve_unleashed.utils import softmax
    x = np.array([1.0, 2.0, 3.0])
    result = softmax(x)
    assert len(result) == 3
    assert abs(result.sum() - 1.0) < 1e-6
    assert result[2] > result[1] > result[0]
    pass  # removed return dict

def test_utils_top_k():
    """Test top_k predictions."""
    from eve_unleashed.utils import top_k
    preds = np.array([0.1, 0.3, 0.05, 0.5, 0.05])
    top = top_k(preds, k=3)
    assert len(top) == 3
    assert top[0][0] == 3  # index 3 has highest value
    assert abs(top[0][1] - 0.5) < 1e-6
    pass  # removed return dict

def test_batch_result():
    """Test BatchResult dataclass."""
    from eve_unleashed.core import BatchResult, InferenceResult
    r1 = InferenceResult("m1", "torch", (1, 256), (1, 10), 5.0, [0.1])
    r2 = InferenceResult("m1", "torch", (1, 256), (1, 10), 6.0, [0.2])
    batch = BatchResult(
        results=[r1, r2],
        total_time_ms=11.0,
        avg_latency_ms=5.5,
        throughput_per_sec=181.8,
    )
    assert len(batch.results) == 2
    assert batch.total_time_ms == 11.0
    assert batch.avg_latency_ms == 5.5
    pass  # removed return dict

def run_all_tests():
    """Run all Eve Unleashed unit tests."""
    print("=" * 60)
    print("EVE UNLEASHED — UNIT TEST SUITE")
    print("=" * 60)
    print()

    tests = [
        ("Config default", test_config_default),
        ("Config GPU preset", test_config_gpu_optimized),
        ("Config CPU preset", test_config_cpu_optimized),
        ("Config serialization", test_config_serialization),
        ("ModelLoader detect", test_model_loader_detect_frameworks),
        ("ModelLoader guess fw", test_model_loader_guess_framework),
        ("InferenceResult", test_inference_result),
        ("BatchResult", test_batch_result),
        ("Engine init", test_engine_init),
        ("Engine list models", test_engine_list_models),
        ("Engine bad framework", test_engine_load_bad_framework),
        ("Engine load missing", test_engine_load_missing_model),
        ("Utils prepare (array)", test_utils_prepare_input_array),
        ("Utils prepare (list)", test_utils_prepare_input_list),
        ("Utils prepare (scalar)", test_utils_prepare_input_scalar),
        ("Utils postprocess torch", test_utils_postprocess_torch),
        ("Utils postprocess sklearn", test_utils_postprocess_sklearn),
        ("Utils validate exact", test_utils_validate_shape_exact),
        ("Utils validate batch", test_utils_validate_shape_batch),
        ("Utils validate mismatch", test_utils_validate_shape_mismatch),
        ("Utils softmax", test_utils_softmax),
        ("Utils top_k", test_utils_top_k),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        print(f"[{name}]\t", end=" ", flush=True)
        try:
            result = test_fn()
            passed += 1
            detail = ""
            if result and "available" in result:
                detail = f" ({', '.join(result['available'][:3])})"
            elif result and "frameworks" in result:
                detail = f" ({', '.join(result['frameworks'][:3])})"
            print(f"PASS{detail}")
        except Exception as e:
            failed += 1
            print(f"FAIL: {e}")

    print()
    print("-" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("-" * 60)

    return passed == len(tests)


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)