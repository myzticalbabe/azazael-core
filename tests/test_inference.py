#!/usr/bin/env python3
"""
Azazael ML Inference Test Suite
Validates actual inference across installed ML frameworks.
"""

import os
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.env

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Skip tests for optional dependencies
try:
    import sklearn
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

try:
    import pandas
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


def test_numpy_inference():
    """Test NumPy array operations and linear algebra inference."""
    import numpy as np

    # Matrix multiplication (common inference op)
    a = np.random.randn(128, 256).astype(np.float32)
    b = np.random.randn(256, 512).astype(np.float32)
    start = time.time()
    c = np.dot(a, b)
    elapsed = time.time() - start
    assert c.shape == (128, 512), f"Shape mismatch: {c.shape}"
    return {
        "framework": "numpy",
        "operation": "matmul(128x256 @ 256x512)",
        "shape": str(c.shape),
        "time_ms": round(elapsed * 1000, 2),
        "status": "PASS",
    }


def test_torch_cpu_inference():
    """Test PyTorch tensor operations and simple neural net inference."""
    pytest.importorskip(
        "torch",
        reason="torch cannot load under free-threading on this interpreter "
               "(C-extension ABI mismatch, not a version check)",
    )
    import torch
    import torch.nn as nn

    # Define a simple MLP
    class SimpleMLP(nn.Module):
        def __init__(self):
            super().__init__()
            self.layers = nn.Sequential(
                nn.Linear(256, 128),
                nn.ReLU(),
                nn.Linear(128, 64),
                nn.ReLU(),
                nn.Linear(64, 10),
            )

        def forward(self, x):
            return self.layers(x)

    model = SimpleMLP()
    model.eval()
    x = torch.randn(32, 256)  # batch of 32

    start = time.time()
    with torch.no_grad():
        output = model(x)
    elapsed = time.time() - start

    assert output.shape == (32, 10), f"Shape mismatch: {output.shape}"
    return {
        "framework": "torch",
        "operation": "MLP(256→128→64→10) inference on batch=32",
        "shape": str(tuple(output.shape)),
        "time_ms": round(elapsed * 1000, 2),
        "status": "PASS",
    }


@pytest.mark.skipif(not HAS_SKLEARN, reason="sklearn not installed")
def test_sklearn_inference():
    """Test sklearn model inference (classification)."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.datasets import make_classification
    import numpy as np

    # Generate synthetic data
    X, y = make_classification(
        n_samples=200, n_features=20, n_informative=10, n_classes=3, random_state=42
    )
    X_train, y_train = X[:150], y[:150]
    X_test = X[150:]

    # Train + inference
    model = RandomForestClassifier(n_estimators=10, random_state=42)
    model.fit(X_train, y_train)

    start = time.time()
    predictions = model.predict(X_test)
    probs = model.predict_proba(X_test)
    elapsed = time.time() - start

    assert len(predictions) == 50, f"Prediction count mismatch: {len(predictions)}"
    assert probs.shape == (50, 3), f"Probability shape mismatch: {probs.shape}"
    return {
        "framework": "sklearn",
        "operation": "RandomForest(10 trees) classification",
        "shape": f"predict={predictions.shape}, proba={probs.shape}",
        "time_ms": round(elapsed * 1000, 2),
        "status": "PASS",
    }


def test_transformers_inference():
    """Test HuggingFace transformers with a small model (needs HF download)."""
    if os.environ.get("AZAZAEL_LIVE_NETWORK") != "1":
        pytest.skip("HF model download needs AZAZAEL_LIVE_NETWORK=1")
    from transformers import pipeline

    # Use a tiny model for sentiment analysis
    classifier = pipeline(
        "sentiment-analysis",
        model="distilbert-base-uncased-finetuned-sst-2-english",
        device=-1,  # CPU
    )

    texts = [
        "Azazael is an incredible ML toolkit!",
        "The code is messy and needs refactoring.",
        "Neutral statement about weather today.",
    ]

    start = time.time()
    results = classifier(texts)
    elapsed = time.time() - start

    assert len(results) == 3, f"Result count mismatch: {len(results)}"
    for r in results:
        assert "label" in r and "score" in r, f"Missing keys in: {r}"
    return {
        "framework": "transformers",
        "operation": "distilbert sentiment on 3 texts",
        "results": [f"{r['label']}({r['score']:.3f})" for r in results],
        "time_ms": round(elapsed * 1000, 2),
        "status": "PASS",
    }


@pytest.mark.skipif(not HAS_PANDAS, reason="pandas not installed")
def test_pandas_inference():
    """Test pandas DataFrame operations (common inference pre/post-processing)."""
    import pandas as pd
    import numpy as np

    # Create a mock inference result DataFrame
    df = pd.DataFrame(
        {
            "class_id": range(5),
            "probability": np.random.rand(5),
            "label": ["cat", "dog", "bird", "fish", "rabbit"],
        }
    )

    # Common inference post-processing
    top = df.nlargest(3, "probability")
    assert len(top) == 3, f"Top-K mismatch: {len(top)}"
    return {
        "framework": "pandas",
        "operation": "top-3 from 5-class probability table",
        "top_classes": top["label"].tolist(),
        "status": "PASS",
    }


def run_all_tests():
    """Run all inference tests and report results."""
    print("=" * 70)
    print("AZAZAEL ML INFERENCE TEST SUITE")
    print("=" * 70)
    print()

    tests = [
        ("NumPy Matrix Ops", test_numpy_inference),
        ("PyTorch Neural Net", test_torch_cpu_inference),
        ("Scikit-Learn RF", test_sklearn_inference),
        ("Transformers NLP", test_transformers_inference),
        ("Pandas Post-Processing", test_pandas_inference),
    ]

    results = []
    passed = 0
    failed = 0

    for name, test_fn in tests:
        print(f"[{name}]", end=" ", flush=True)
        try:
            result = test_fn()
            results.append(result)
            passed += 1
            print(f"PASS ({result.get('time_ms', 'N/A')}ms)")
        except Exception as e:
            failed += 1
            results.append({"framework": name, "status": "FAIL", "error": str(e)})
            print(f"FAIL: {e}")

    print()
    print("-" * 70)
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("-" * 70)

    # Detailed results
    print()
    for r in results:
        status = r["status"]
        framework = r.get("framework", "unknown")
        operation = r.get("operation", "N/A")
        print(f"  [{status}] {framework}: {operation}")
        if status == "FAIL":
            print(f"         Error: {r.get('error', 'unknown')}")
        elif "results" in r:
            print(f"         Output: {r['results']}")
        elif "top_classes" in r:
            print(f"         Top: {r['top_classes']}")

    print()
    print("=" * 70)
    print("INFERENCE TEST SUITE COMPLETE")
    print("=" * 70)

    return passed == len(tests)


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
