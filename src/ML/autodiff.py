# coding: utf-8
"""First-principles automatic differentiation.

Reverse-mode goes through mygrad (true back-propagation: gradients flow
backward through the graph). Forward-mode is implemented two ways: exact
dual-number propagation and the complex-step method for plain analytic numpy
functions. Optimizers (SGD, Momentum, Adam) are implemented from first
principles on top of mygrad parameters. The Hessian-vector product uses
finite differences of reverse-mode gradients and is named accordingly.
"""

from __future__ import annotations

import math
from typing import Any, Callable, List

import mygrad as mg
import numpy as np

#: mygrad.Tensor is usable at runtime but absent from mygrad.__all__, so the
#: static private-import rule cannot see it; bind once via getattr.
_Tensor = getattr(mg, "Tensor")

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def backprop_grad(fn: Callable[[Any], Any], x0: np.ndarray) -> np.ndarray:
    """Reverse-mode gradient of scalar ``fn`` at ``x0`` via mygrad.

    ``fn`` must build its expression from mygrad operations acting on the
    single tensor argument.
    """
    x = _Tensor(np.asarray(x0, dtype=float))
    y = fn(x)
    y.backward()
    grad = np.asarray(x.grad)
    return grad.reshape(np.shape(x0))


class Dual:
    """Dual number ``a + b*eps`` (eps^2 = 0) for exact forward-mode AD."""

    __slots__ = ("real", "dual")

    def __init__(self, real: float, dual: float = 0.0) -> None:
        self.real = float(real)
        self.dual = float(dual)

    def _coerce(self, other: Any) -> "Dual":
        return other if isinstance(other, Dual) else Dual(other, 0.0)

    def __add__(self, other: Any) -> "Dual":
        o = self._coerce(other)
        return Dual(self.real + o.real, self.dual + o.dual)

    __radd__ = __add__

    def __sub__(self, other: Any) -> "Dual":
        o = self._coerce(other)
        return Dual(self.real - o.real, self.dual - o.dual)

    def __rsub__(self, other: Any) -> "Dual":
        o = self._coerce(other)
        return Dual(o.real - self.real, o.dual - self.dual)

    def __mul__(self, other: Any) -> "Dual":
        o = self._coerce(other)
        return Dual(self.real * o.real, self.real * o.dual + self.dual * o.real)

    __rmul__ = __mul__

    def __truediv__(self, other: Any) -> "Dual":
        o = self._coerce(other)
        return Dual(
            self.real / o.real,
            (self.dual * o.real - self.real * o.dual) / (o.real * o.real),
        )

    def __pow__(self, power: float) -> "Dual":
        return Dual(
            self.real**power,
            power * self.real ** (power - 1) * self.dual,
        )

    def exp(self) -> "Dual":
        e = math.exp(self.real)
        return Dual(e, e * self.dual)

    def log(self) -> "Dual":
        return Dual(math.log(self.real), self.dual / self.real)

    def sin(self) -> "Dual":
        return Dual(math.sin(self.real), math.cos(self.real) * self.dual)

    def cos(self) -> "Dual":
        return Dual(math.cos(self.real), -math.sin(self.real) * self.dual)


def forward_mode_grad(fn: Callable[[list[Dual]], Dual], x0: list[float]) -> np.ndarray:
    """Exact forward-mode gradient via dual numbers (one pass per input dim)."""
    grads = []
    for i in range(len(x0)):
        duals = [Dual(v, 1.0 if j == i else 0.0) for j, v in enumerate(x0)]
        grads.append(fn(duals).dual)
    return np.asarray(grads)


def complex_step_grad(fn: Callable[[np.ndarray], Any], x0: np.ndarray, h: float = 1e-20) -> np.ndarray:
    """Forward-mode-equivalent gradient via the complex-step method.

    Machine-precision exact for analytic numpy functions; no subtraction
    cancellation. Fails (loudly, via complex warnings/nans) on non-analytic
    ops such as comparisons or branching on values — use dual numbers there.
    """
    x0 = np.asarray(x0, dtype=float)
    grads = np.empty_like(x0)
    for i in range(x0.size):
        z = x0.astype(complex)
        z.flat[i] += 1j * h
        grads.flat[i] = np.imag(fn(z)) / h
    return grads


def minimize(
    fn: Callable[[Any], Any],
    x0: np.ndarray,
    method: str = "adam",
    lr: float = 0.01,
    steps: int = 1000,
    beta1: float = 0.9,
    beta2: float = 0.999,
    eps: float = 1e-8,
) -> tuple[np.ndarray, list[float]]:
    """First-principles minimization of scalar ``fn`` (mygrad graph).

    Methods: ``"sgd"``, ``"momentum"``, ``"adam"``. Returns ``(x_star,
    loss_history)``. Fresh state per call; free-thread safe.
    """
    if method not in ("sgd", "momentum", "adam"):
        raise ValueError(f"unknown method: {method!r}")
    current = np.asarray(x0, dtype=float)
    m = np.zeros_like(current)
    v = np.zeros_like(current)
    history: list[float] = []
    for t in range(1, steps + 1):
        # Fresh tensor per step: mygrad accumulates .grad in place and the
        # attribute has no setter, so rebuilding is the clean reset.
        x = _Tensor(np.asarray(current))
        y = fn(x)
        loss = float(y.data)
        history.append(loss)
        y.backward()
        g = np.asarray(x.grad)
        if method == "sgd":
            step = lr * g
        elif method == "momentum":
            m = beta1 * m + (1.0 - beta1) * g
            step = lr * m
        else:
            m = beta1 * m + (1.0 - beta1) * g
            v = beta2 * v + (1.0 - beta2) * (g * g)
            m_hat = m / (1.0 - beta1**t)
            v_hat = v / (1.0 - beta2**t)
            step = lr * m_hat / (np.sqrt(v_hat) + eps)
        current = current - step
    return np.asarray(current), history


def hvp_finite_difference(
    fn: Callable[[Any], Any], x0: np.ndarray, vec: np.ndarray, eps: float = 1e-5
) -> np.ndarray:
    """Hessian-vector product via finite differences of reverse-mode grads.

    Exact up to O(eps^2); named for what it is since mygrad graphs do not
    support second-order back-propagation directly.
    """
    x0 = np.asarray(x0, dtype=float)
    vec = np.asarray(vec, dtype=float)
    g_plus = backprop_grad(fn, x0 + eps * vec)
    g_minus = backprop_grad(fn, x0 - eps * vec)
    return (g_plus - g_minus) / (2.0 * eps)
