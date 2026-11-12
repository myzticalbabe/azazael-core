"""
Eve Unleashed Utilities
========================
Tensor preparation, output postprocessing, and shape validation.
"""

from typing import Any, List

import numpy as np

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def prepare_input_tensor(
    inputs: np.ndarray | list | float | int
) -> "np.ndarray":
    """Convert various input formats to a numpy array suitable for inference."""
    if isinstance(inputs, np.ndarray):
        arr = inputs
    elif isinstance(inputs, list):
        arr = np.array(inputs, dtype=np.float32)
    elif isinstance(inputs, (int, float)):
        arr = np.array([[inputs]], dtype=np.float32)
    else:
        raise TypeError(f"Unsupported input type: {type(inputs)}")

    # Ensure at least 2D
    if arr.ndim == 1:  # pyright: ignore[reportAttributeAccessIssue]
        arr = arr.reshape(1, -1)  # pyright: ignore[reportAttributeAccessIssue]

    return arr.astype(np.float32)  # pyright: ignore[reportAttributeAccessIssue]


def postprocess_output(
    output: Any, framework: str
) -> list[Any]:
    """Convert framework-specific output to a standard list format."""
    if framework == "torch":
        return output.cpu().numpy().tolist()
    elif framework == "sklearn":
        if isinstance(output, np.ndarray):
            return output.tolist()
        return list(output) if hasattr(output, "__iter__") else [output]
    elif framework == "transformers":
        return output if isinstance(output, list) else [output]
    else:
        return list(output) if hasattr(output, "__iter__") else [output]


def validate_input_shape(
    inputs: np.ndarray,
    expected_shape: tuple,
    allow_batch: bool = True,
) -> bool:
    """Validate that input matches the expected shape."""
    if inputs.shape == expected_shape:
        return True
    if allow_batch and inputs.shape[1:] == expected_shape:
        return True
    return False


def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Compute softmax values for a set of scores."""
    e_x = np.exp(x - np.max(x, axis=axis, keepdims=True))
    return e_x / e_x.sum(axis=axis, keepdims=True)


def top_k(
    predictions: np.ndarray, k: int = 5
) -> list[tuple]:
    """Get top-K predictions with indices."""
    indices = np.argsort(predictions)[-k:][::-1]
    values = predictions[indices]
    return [(int(i), float(v)) for i, v in zip(indices, values)]
