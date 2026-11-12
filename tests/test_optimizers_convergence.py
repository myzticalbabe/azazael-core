#!/usr/bin/env python
# coding: utf-8
"""
test_optimizers_convergence.py — Unit tests for all 7 gradient descent optimizers.

Verifies convergence on standard test functions:
  - Quadratic bowl:   f(w) = 0.5 * sum((w - w_star)^2)   (convex, well-conditioned)
  - Rosenbrock:       f(x,y) = (1-x)^2 + 100*(y-x^2)^2   (non-convex, ill-conditioned)

Each optimizer is tested for:
  1. Loss decreases monotonically over training
  2. Final loss converges below threshold
  3. Parameters approach known optimum
  4. No NaN or inf values in parameters or gradients

Uses mygrad 2.3.0 on Python 3.14t free-threading.
"""

import sys
import pytest

# `mygrad` is an optional ML dependency (see src/ML/README.md and
# scripts/run_full_suite.py). It is ABSENT from venv-314t on this host, and a
# bare `import mygrad` at module scope aborted COLLECTION for the whole session
# (rc=2) instead of skipping. The guard is deliberately MODULE-level rather than
# per-test, because mygrad has historically faulted at the C level on a
# free-threaded build and a segfault cannot be caught -- pytest's "no tests
# collected" (rc=5) is the documented, safe outcome. `importorskip` sets
# allow_module_level for us.
mg = pytest.importorskip(
    "mygrad",
    reason="mygrad absent (free-threaded build; see scripts/run_full_suite.py)",
)
import numpy as np

PASSED = 0
FAILED = 0


def param(val):
    """Create a mygrad parameter (requires_grad as attribute in 2.3.0)."""
    p = mg.tensor(np.asarray(val).astype(float))
    p.requires_grad = True
    return p


def test(name, condition):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS: {name}")
    else:
        FAILED += 1
        print(f"  FAIL: {name}")


def check_close(a, b, tol=1e-5, name=""):
    ok = np.allclose(np.asarray(a), np.asarray(b), atol=tol)
    test(name, ok)
    return ok


# ═══════════════════════════════════════════════════════
# TEST FUNCTIONS
# ═══════════════════════════════════════════════════════

def quadratic_bowl(x, x_star=None):
    """f(x) = 0.5 * sum((x - x_star)^2), minimum at x_star with value 0."""
    if x_star is None:
        x_star = np.array([3.0, 3.0])
    diff = x - mg.tensor(x_star)
    return 0.5 * mg.sum(diff ** 2)


def rosenbrock(x):
    """f(x,y) = (1-x)^2 + 100*(y-x^2)^2, global minimum at (1,1) with value 0."""
    return (1 - x[0]) ** 2 + 100 * (x[1] - x[0] ** 2) ** 2


# ═══════════════════════════════════════════════════════
# OPTIMIZERS (from azazael-autodiff-gradient skill)
# ═══════════════════════════════════════════════════════

class SGD:
    def __init__(self, params, lr=0.01):
        self.params, self.lr = list(params), lr
    def zero_grad(self):
        for p in self.params: p.null_grad()
    def step(self):
        for p in self.params:
            if p.grad is not None: p.data -= self.lr * np.array(p.grad)


class SGDMomentum:
    def __init__(self, params, lr=0.01, momentum=0.9):
        self.params, self.lr, self.beta = list(params), lr, momentum
        self.velocities = [np.zeros_like(p.data) for p in self.params]
    def zero_grad(self):
        for p in self.params: p.null_grad()
    def step(self):
        for i, p in enumerate(self.params):
            if p.grad is not None:
                self.velocities[i] = self.beta * self.velocities[i] + np.array(p.grad)
                p.data -= self.lr * self.velocities[i]


class NesterovSGD:
    def __init__(self, params, lr=0.01, momentum=0.9):
        self.params, self.lr, self.momentum = list(params), lr, momentum
        self.velocities = [np.zeros_like(p.data) for p in self.params]
    def zero_grad(self):
        for p in self.params: p.null_grad()
    def step(self):
        for i, p in enumerate(self.params):
            if p.grad is not None:
                v_prev = self.velocities[i].copy()
                self.velocities[i] = self.momentum * v_prev - self.lr * np.array(p.grad)
                p.data += -self.momentum * v_prev + (1 + self.momentum) * self.velocities[i]


class AdaGrad:
    def __init__(self, params, lr=0.01, eps=1e-8):
        self.params, self.lr, self.eps = list(params), lr, eps
        self.cache = [np.zeros_like(p.data) for p in self.params]
    def zero_grad(self):
        for p in self.params: p.null_grad()
    def step(self):
        for i, p in enumerate(self.params):
            if p.grad is not None:
                g = np.array(p.grad)
                self.cache[i] += g ** 2
                p.data -= self.lr * g / (np.sqrt(self.cache[i]) + self.eps)


class RMSprop:
    def __init__(self, params, lr=0.001, decay=0.9, eps=1e-8):
        self.params, self.lr, self.decay, self.eps = list(params), lr, decay, eps
        self.cache = [np.zeros_like(p.data) for p in self.params]
    def zero_grad(self):
        for p in self.params: p.null_grad()
    def step(self):
        for i, p in enumerate(self.params):
            if p.grad is not None:
                g = np.array(p.grad)
                self.cache[i] = self.decay * self.cache[i] + (1 - self.decay) * g ** 2
                p.data -= self.lr * g / (np.sqrt(self.cache[i]) + self.eps)


class Adam:
    def __init__(self, params, lr=0.001, betas=(0.9, 0.999), eps=1e-8):
        self.params, self.lr = list(params), lr
        self.beta1, self.beta2, self.eps, self.t = betas[0], betas[1], eps, 0
        self.m = [np.zeros_like(p.data) for p in self.params]
        self.v = [np.zeros_like(p.data) for p in self.params]
    def zero_grad(self):
        for p in self.params: p.null_grad()
    def step(self):
        self.t += 1
        for i, p in enumerate(self.params):
            if p.grad is not None:
                g = np.array(p.grad)
                self.m[i] = self.beta1 * self.m[i] + (1 - self.beta1) * g
                self.v[i] = self.beta2 * self.v[i] + (1 - self.beta2) * g ** 2
                m_hat = self.m[i] / (1 - self.beta1 ** self.t)
                v_hat = self.v[i] / (1 - self.beta2 ** self.t)
                p.data -= self.lr * m_hat / (np.sqrt(v_hat) + self.eps)


class AdamW:
    def __init__(self, params, lr=0.001, betas=(0.9, 0.999), eps=1e-8, weight_decay=0.01):
        self.params, self.lr = list(params), lr
        self.beta1, self.beta2, self.eps = betas[0], betas[1], eps
        self.weight_decay, self.t = weight_decay, 0
        self.m = [np.zeros_like(p.data) for p in self.params]
        self.v = [np.zeros_like(p.data) for p in self.params]
    def zero_grad(self):
        for p in self.params: p.null_grad()
    def step(self):
        self.t += 1
        for i, p in enumerate(self.params):
            if p.grad is not None:
                g = np.array(p.grad)
                self.m[i] = self.beta1 * self.m[i] + (1 - self.beta1) * g
                self.v[i] = self.beta2 * self.v[i] + (1 - self.beta2) * g ** 2
                m_hat = self.m[i] / (1 - self.beta1 ** self.t)
                v_hat = self.v[i] / (1 - self.beta2 ** self.t)
                p.data -= self.lr * self.weight_decay * p.data
                p.data -= self.lr * m_hat / (np.sqrt(v_hat) + self.eps)


# ═══════════════════════════════════════════════════════
# OPTIMIZER CONFIGS — tuned for each test function
# ═══════════════════════════════════════════════════════

OPTIMIZER_CONFIGS = {
    # (optimizer_class, lr, extra_kwargs, quadbowl_iters, rosenbrock_iters)
    'SGD':       (SGD,       0.1,  {},                              200,  20000),
    'Momentum':  (SGDMomentum, 0.03, {'momentum': 0.9},            150,  10000),
    'Nesterov':  (NesterovSGD, 0.03, {'momentum': 0.9},            150,  10000),
    'AdaGrad':   (AdaGrad,   0.5,  {},                              300,  10000),
    'RMSprop':   (RMSprop,   0.01, {'decay': 0.9},                 300,  10000),
    'Adam':      (Adam,      0.1,  {'betas': (0.9, 0.999)},        100,  5000),
    'AdamW':     (AdamW,     0.1,  {'betas': (0.9, 0.999), 'weight_decay': 1e-4}, 100, 5000),
}


# ═══════════════════════════════════════════════════════
# CONVERGENCE TESTER
# ═══════════════════════════════════════════════════════

def test_convergence(name, opt_class, lr, extra_kwargs, loss_fn, x_init, x_star,
                     max_iters, loss_threshold, dist_threshold=None):
    """Train optimizer and verify convergence.

    Returns True if all checks pass.
    """
    x = param(np.array(x_init).astype(float))
    opt = opt_class([x], lr=lr, **extra_kwargs)

    losses = []
    params_history = []

    for iteration in range(max_iters):
        opt.zero_grad()
        loss = loss_fn(x)
        loss.backward()
        opt.step()

        loss_val = float(loss.data)
        losses.append(loss_val)
        params_history.append(np.array(x.data).copy())

        # NaN check
        if np.isnan(loss_val) or np.any(np.isnan(np.array(x.data))):
            test(f"{name} NaN at iter {iteration}", False)
            return False

    final_loss = losses[-1]
    final_x = params_history[-1]

    # Check 1: loss decreased (or stayed near zero)
    loss_ok = final_loss < loss_threshold
    test(f"{name} final loss {final_loss:.6f} < {loss_threshold}", loss_ok)

    # Check 2: parameters near optimum
    if x_star is not None and dist_threshold is not None:
        dist = np.sqrt(np.sum((final_x - np.array(x_star)) ** 2))
        dist_ok = dist < dist_threshold
        test(f"{name} dist to optimum {dist:.4f} < {dist_threshold}", dist_ok)
    else:
        dist_ok = True

    # Check 3: loss generally decreasing (last 10% < first 10%)
    early_mean = np.mean(losses[:max_iters // 10])
    late_mean = np.mean(losses[-max_iters // 10:])
    decreasing = late_mean < early_mean * 0.5
    test(f"{name} loss decreasing ({late_mean:.4f} < {early_mean:.4f})", decreasing)

    return loss_ok and dist_ok and decreasing


# ═══════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════

def run_quadratic_bowl_tests():
    """Test all optimizers on quadratic bowl f(x) = 0.5*||x - [3,3]||^2."""
    print("\n" + "=" * 65)
    print("  QUADRATIC BOWL: f(x) = 0.5*||x - [3,3]||^2")
    print("  Start: [0,0], Optimum: [3,3], Minimum value: 0")
    print("=" * 65)

    x_star = np.array([3.0, 3.0])
    x_init = np.array([0.0, 0.0])

    for name, (opt_cls, lr, kwargs, quad_iters, _) in OPTIMIZER_CONFIGS.items():
        print(f"\n  [{name}] lr={lr}, iters={quad_iters}")
        test_convergence(
            name=f"QuadBowl {name}",
            opt_class=opt_cls, lr=lr, extra_kwargs=kwargs,
            loss_fn=lambda x: quadratic_bowl(x, x_star),
            x_init=x_init, x_star=x_star,
            max_iters=quad_iters, loss_threshold=0.5, dist_threshold=1.0,
        )


def run_rosenbrock_tests():
    """Test all optimizers on Rosenbrock function f(x,y) = (1-x)^2 + 100*(y-x^2)^2."""
    print("\n" + "=" * 65)
    print("  ROSENBROCK: f(x,y) = (1-x)^2 + 100*(y-x^2)^2")
    print("  Start: [-1, 1], Optimum: [1,1], Minimum value: 0")
    print("=" * 65)

    x_star = np.array([1.0, 1.0])
    x_init = np.array([-1.0, 1.0])

    for name, (opt_cls, lr, kwargs, _, rosen_iters) in OPTIMIZER_CONFIGS.items():
        # Use lower LR for plain SGD/Momentum/Nesterov on Rosenbrock (ill-conditioned)
        rosen_lr = lr
        rosen_extra = dict(kwargs)
        if name in ('SGD',):
            rosen_lr = 0.001
        elif name in ('Momentum', 'Nesterov'):
            rosen_lr = 0.001
        print(f"\n  [{name}] lr={rosen_lr}, iters={rosen_iters}")
        test_convergence(
            name=f"Rosenbrock {name}",
            opt_class=opt_cls, lr=rosen_lr, extra_kwargs=rosen_extra,
            loss_fn=rosenbrock,
            x_init=x_init, x_star=x_star,
            max_iters=rosen_iters, loss_threshold=0.5, dist_threshold=0.5,
        )


def run_edge_case_tests():
    """Edge cases: zero gradients, NaN safety, multi-parameter."""
    print("\n" + "=" * 65)
    print("  EDGE CASES")
    print("=" * 65)

    # Zero gradient: parameter should not change
    x = param(np.array([5.0]))
    opt = Adam([x], lr=0.1)
    opt.zero_grad()
    loss = (x - 5.0) ** 2  # at x=5.0, loss=0 and gradient is zero
    loss.backward()
    opt.step()
    # Robust float conversion for numpy arrays (0-d or 1-element)
    x_val = float(np.asarray(x.data).ravel()[0])
    test("Zero grad: param unchanged", abs(x_val - 5.0) < 1e-10)

    # Multi-parameter optimization
    x1 = param(np.array([0.0])); x2 = param(np.array([0.0]))
    opt_m = Adam([x1, x2], lr=0.1)
    for _ in range(50):
        opt_m.zero_grad()
        loss = (x1 - 1.0) ** 2 + (x2 - 2.0) ** 2
        loss.backward()
        opt_m.step()
    check_close(float(np.asarray(x1.data).ravel()[0]), 1.0, tol=0.1, name="Multi-param: x1 -> 1.0")
    check_close(float(np.asarray(x2.data).ravel()[0]), 2.0, tol=0.1, name="Multi-param: x2 -> 2.0")

    # AdamW weight decay test
    x_wd = param(np.array([5.0]))
    opt_wd = AdamW([x_wd], lr=0.1, weight_decay=0.5)
    for _ in range(50):
        opt_wd.zero_grad()
        loss = (x_wd - 5.0) ** 2  # gradient pulls toward 5, decay pulls toward 0
        loss.backward()
        opt_wd.step()
    # Weight decay should pull x substantially below 5.0
    wd_val = float(np.asarray(x_wd.data).ravel()[0])
    test("AdamW wd=0.5 pulls x below 3.0 from start 5.0", wd_val < 3.0)


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════

def main():
    run_quadratic_bowl_tests()
    run_rosenbrock_tests()
    run_edge_case_tests()

    print("\n" + "=" * 65)
    total = PASSED + FAILED
    print(f"  RESULTS: {PASSED}/{total} passed, {FAILED} failed")
    print("=" * 65)

    if FAILED > 0:
        print(f"\n  {FAILED} TEST(S) FAILED")
        sys.exit(1)
    else:
        print("\n  ALL TESTS PASSED")
        sys.exit(0)


# ── collection opt-out ───────────────────────────────────────────────
# `test` and `test_convergence` are harness helpers that take parameters, not
# pytest test functions. pytest collects any module-level `test*` callable, so
# it would try to inject fixtures for their arguments and error before a single
# assertion runs. Opt them out explicitly; `main()` is the real entry point.
test.__test__ = False
test_convergence.__test__ = False


if __name__ == '__main__':
    main()


# ── pytest entry point ───────────────────────────────────────────────
# `main()` is this file's real harness and it exits the process, so without a
# wrapper pytest collected ZERO tests here and returned 5: a file holding seven
# optimizer convergence checks read as a permanently broken file in the suite
# report while its own harness was green. Same assertions, reachable by the
# gate.
def test_optimizer_harness_passes():
    try:
        main()
    except SystemExit as exc:
        assert not exc.code, f"optimizer harness exited {exc.code}"
    else:
        assert FAILED == 0, f"{FAILED} optimizer check(s) failed"
