# coding: utf-8
"""
CORE DEVICE — accelerator abstraction for the evolution stack.

Preference order: Intel XPU (torch 2.14.0+xpu) > CUDA > CPU.

IMPORTANT (measured 2026-09-12 on LAPTOP-8U5MSH3G, re-checked 2026-10-19):
torch is a GIL-ABI wheel, so its extension cannot load under a free-threaded
interpreter whose ABI differs from the wheel's tag. The 2026-09-12 measurement
was taken on the now-RETIRED 3.14t interpreter (python3.14t.exe), where `import
torch` failed; the 3.15t production venv (C:/Azazael/.venvs/py315t) carries a
torch build that imports there. The guarantee this module keeps is therefore
unchanged and not tied to a version: never hard-fail on import — every
torch-dependent path degrades to "unavailable" and CPU/numpy callers keep
working, because a device probe can fault at C level even when the import
succeeds.

The two interpreters also do not share live objects. Anything crossing between
them goes through durable artefacts, never through an imported module handle.
"""

from __future__ import annotations

import os
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "torch_available", "numpy_available", "get_device", "device_label",
    "describe", "to_device", "empty_cache", "synchronize",
    "suggest_batch_size", "DEVICE_ENV",
]

# Override with AZAZAEL_DEVICE=cpu|xpu|cuda when debugging or reproducing CPU runs.
DEVICE_ENV = "AZAZAEL_DEVICE"

_TORCH = None
_TORCH_ERR: str | None = None
_NUMPY = None
_NUMPY_ERR: str | None = None
_RESOLVED: Any = None


def _load_torch():
    """Import torch once; cache the failure reason instead of raising."""
    global _TORCH, _TORCH_ERR
    if _TORCH is not None or _TORCH_ERR is not None:
        return _TORCH
    if os.environ.get(DEVICE_ENV, "").strip().lower() == "cpu":
        _TORCH_ERR = "disabled by %s=cpu" % DEVICE_ENV
        return None
    try:
        import torch  # noqa: PLC0415
        _TORCH = torch
    except Exception as exc:  # ImportError on an ABI mismatch, OSError on a broken install
        note_failure("device:L50", exc)
        _TORCH_ERR = "%s: %s" % (type(exc).__name__, exc)
        _TORCH = None
    return _TORCH


def _load_numpy():
    global _NUMPY, _NUMPY_ERR
    if _NUMPY is not None or _NUMPY_ERR is not None:
        return _NUMPY
    try:
        import numpy as np  # noqa: PLC0415
        _NUMPY = np
    except Exception as exc:
        note_failure("device:L63", exc)
        _NUMPY_ERR = "%s: %s" % (type(exc).__name__, exc)
        _NUMPY = None
    return _NUMPY


def torch_available() -> bool:
    """True when torch imported *and* it exposes a working backend."""
    return _load_torch() is not None


def numpy_available() -> bool:
    return _load_numpy() is not None


def _torch_error() -> str:
    _load_torch()
    return _TORCH_ERR or "unknown"


def get_device():
    """Resolve the best available device.

    Returns a torch.device when torch is usable, else the string 'cpu' so
    callers can treat the result opaquely and still run pure-numpy paths.
    """
    global _RESOLVED
    if _RESOLVED is not None:
        return _RESOLVED

    torch = _load_torch()
    if torch is None:
        _RESOLVED = "cpu"
        return _RESOLVED

    want = os.environ.get(DEVICE_ENV, "").strip().lower()
    if want in ("cpu",):
        _RESOLVED = torch.device("cpu")
        return _RESOLVED

    # Intel XPU first: this machine has an Arc 140T and no NVIDIA hardware.
    try:
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            _RESOLVED = torch.device("xpu:0")
            return _RESOLVED
    except Exception as exc:
        note_failure("device:L108", exc)

    try:
        if torch.cuda.is_available():
            _RESOLVED = torch.device("cuda:0")
            return _RESOLVED
    except Exception as exc:
        note_failure("device:L115", exc)

    _RESOLVED = torch.device("cpu")
    return _RESOLVED


def device_label() -> str:
    """Short human label: 'xpu', 'cuda' or 'cpu'."""
    dev = get_device()
    if isinstance(dev, str):
        return dev
    return str(dev).split(":")[0]


def to_device(obj, dtype=None):
    """Move a tensor/array/module to the resolved device. No-op without torch."""
    torch = _load_torch()
    if torch is None:
        return obj
    dev = get_device()
    if isinstance(dev, str):
        return obj
    try:
        if dtype is not None:
            return obj.to(dev, dtype)
        return obj.to(dev)
    except Exception as exc:
        note_failure("device:L142", exc)
        return obj


def synchronize() -> None:
    """Block until queued device work completes (timing correctness)."""
    torch = _load_torch()
    if torch is None:
        return
    dev = device_label()
    try:
        if dev == "xpu":
            torch.xpu.synchronize()
        elif dev == "cuda":
            torch.cuda.synchronize()
    except Exception as exc:
        note_failure("device:L157", exc)


def empty_cache() -> None:
    torch = _load_torch()
    if torch is None:
        return
    try:
        if device_label() == "xpu":
            torch.xpu.empty_cache()
        elif device_label() == "cuda":
            torch.cuda.empty_cache()
    except Exception as exc:
        note_failure("device:L170", exc)


def describe() -> dict:
    """Diagnostics for the wallboard and the audit trail."""
    info: dict = {
        "torch_available": torch_available(),
        "numpy_available": numpy_available(),
        "device": device_label(),
        "env_override": os.environ.get(DEVICE_ENV, ""),
    }
    torch = _load_torch()
    if torch is None:
        info["torch_error"] = _torch_error()
        return info

    info["torch_version"] = torch.__version__
    try:
        info["device_count"] = torch.xpu.device_count() if hasattr(torch, "xpu") else 0
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            props = torch.xpu.get_device_properties(0)
            info["device_name"] = props.name
            info["total_memory_gib"] = round(
                getattr(props, "total_memory", 0) / 2 ** 30, 2)
    except Exception as exc:
        note_failure("device:L195", exc)
        info["device_error"] = str(exc)[:160]
    return info


def suggest_batch_size(base: int = 64, cap: int = 512) -> int:
    """Batch size scaled to available accelerator memory.

    The Arc 140T reports ~7.8 GiB but SHARES it with 16 GB of system RAM, so
    this deliberately stays conservative rather than sizing to the reported
    figure.
    """
    torch = _load_torch()
    if torch is None or device_label() == "cpu":
        return max(1, min(base, cap))
    try:
        mem_gib = getattr(torch.xpu.get_device_properties(0), "total_memory", 0) / 2 ** 30
    except Exception as exc:
        note_failure("device:L212", exc)
        mem_gib = 0.0
    if mem_gib <= 0:
        return base
    scaled = int(base * max(0.5, min(4.0, mem_gib / 4.0)))
    return max(1, min(scaled, cap))
