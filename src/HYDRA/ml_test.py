# coding: utf-8
"""
HYDRA Cluster ML Execution Test
Tests basic ML operations with available frameworks.
"""

import sys
import time
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def test_numpy():
    """Test NumPy operations."""
    print("\n[NUMPY] Testing...")
    import numpy as np

    arr = np.random.rand(1000, 1000)
    result = np.linalg.svd(arr, full_matrices=False)
    print(f"  [OK] SVD decomposition: {result[1].shape}")

    matrix = np.random.rand(100, 100)
    eigenvalues = np.linalg.eigvals(matrix)
    print(f"  [OK] Eigenvalues: {len(eigenvalues)} computed")

    return True


def test_scipy():
    """Test SciPy operations."""
    print("\n[SCIPY] Testing...")
    import numpy as np
    from scipy.sparse import csr_matrix

    size = 1000
    data = np.random.rand(size)
    sparse_mat = csr_matrix(data)
    print(f"  [OK] Sparse matrix: {sparse_mat.shape}")

    from scipy import stats

    result = stats.norm.rvs(size=1000)
    print(f"  [OK] Statistical sampling: {len(np.asarray(result))} samples")

    return True


def test_torch():
    """Test PyTorch operations."""
    print("\n[PYTORCH] Testing...")
    import torch

    x = torch.randn(100, 100)
    y = torch.randn(100, 100)
    z = torch.mm(x, y)
    print(f"  [OK] Matrix multiplication: {z.shape}")

    if torch.cuda.is_available():
        print("  [OK] CUDA available")
    else:
        print("  [OK] CPU mode (CUDA not available)")

    return True


def test_tensorflow():
    """Test TensorFlow operations."""
    print("\n[TENSORFLOW] Testing...")
    import tensorflow as tf  # type: ignore[reportMissingModuleSource]

    x = tf.random.normal([100, 100])
    y = tf.random.normal([100, 100])
    z = tf.matmul(x, y)
    print(f"  [OK] Matrix multiplication: {z.shape}")

    return True


def test_sklearn():
    """Test scikit-learn operations."""
    print("\n[SKLEARN] Testing...")
    import numpy as np
    from sklearn.decomposition import PCA
    from sklearn.datasets import make_classification

    X, y = make_classification(
        n_samples=1000, n_features=100, n_classes=5, n_informative=5
    )
    X = np.asarray(X)
    pca = PCA(n_components=10)
    X_pca = pca.fit_transform(X)
    print(f"  [OK] PCA: {X.shape} -> {X_pca.shape}")

    from sklearn.ensemble import RandomForestClassifier

    clf = RandomForestClassifier(n_estimators=10)
    clf.fit(X, y)
    score = clf.score(X, y)
    print(f"  [OK] Random Forest: accuracy = {score:.3f}")

    return True


def main():
    """Run all ML tests."""
    print("=" * 60)
    print("  HYDRA CLUSTER - ML EXECUTION TEST")
    print("=" * 60)

    start = time.time()
    results = {}

    tests = [
    ("numpy", test_numpy),
    ("scipy", test_scipy),
    ("torch", test_torch),
    ("tensorflow", test_tensorflow),
    ("sklearn", test_sklearn),
    ]

    for name, test_func in tests:
        try:
            results[name] = test_func()
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            results[name] = False

            duration = time.time() - start

            print("\n" + "=" * 60)
            print("  ML TEST RESULTS")
            print("=" * 60)

            passed = sum(1 for v in results.values() if v)
            total = len(results)

            print(f"Tests Passed: {passed}/{total}")
            print(f"Duration: {duration:.2f}s")

            for name, passed in results.items():
                status = "[OK]" if passed else "[FAIL]"
                print(f"  {status} {name}")

                print("=" * 60)

                return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
