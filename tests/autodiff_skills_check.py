#!/usr/bin/env python
# coding: utf-8
"""
test_autodiff_skills.py — End-to-end verification of all 4 mygrad autodiff skills.

Exercises every pattern from:
  - azazael-autodiff-forward (JVP scalar, Jacobian via input seeding, HVP)
  - azazael-autodiff-backprop (gradient, seeds, batch, no-autodiff, clipping, accumulation)
  - azazael-autodiff-gradient (7 optimizers + 3 LR schedules + ReduceLROnPlateau)
  - azazael-autodiff-higher-order (Hessian, Jacobian, Gauss-Newton, CG Newton, derivatives)

Uses mygrad 2.3.0 on Python 3.14t free-threading.
NOTE: mygrad 2.3.0 limitation — .backward(seed) on vector outputs does not
      populate .grad correctly. Vector JVP tested via scalar-only fallback.
"""

import sys
import sysconfig

# mygrad pulls in numba, and numba's cp314t extension faults at C level inside
# a free-threaded process. A segfault cannot be caught, so a try/except around
# `import mygrad` would never get to run -- the runtime has to be checked
# first. (The docstring's older claim that mygrad 2.3.0 runs on 3.14t is
# superseded by that measurement.) Run this file with python.exe instead.
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    print("SKIP: mygrad -> numba cannot be imported in a free-threaded build "
          "(numba's cp314t extension faults at C level).")
    print("      Re-run under the GIL interpreter: python tests/autodiff_skills_check.py")
    sys.exit(0)

import mygrad as mg
import numpy as np

np.random.seed(42)  # reproducibility

PASSED = 0
FAILED = 0

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


# ═══════════════════════════════════════════
# HELPER: Parameter creation (mygrad 2.3.0)
# ═══════════════════════════════════════════
def param(val):
    p = mg.tensor(np.asarray(val).astype(float))
    p.requires_grad = True
    return p


# ═══════════════════════════════════════════
# SECTION 1: Forward-Mode AD
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print("SECTION 1: Forward-Mode AD")
print("=" * 60)


def jvp_scalar(f, x, v):
    """JVP for scalar f: grad_f(x) dot v."""
    x = np.asarray(x); v = np.asarray(v)
    xt = mg.tensor(x); xt.requires_grad = True
    yt = f(xt); yt.backward()
    return float(np.dot(xt.grad, v))


def jacobian_via_seeding(f, x):
    """Jacobian via input-by-input seeding (for scalar f).

    For scalar f: R^n -> R, returns 1xn gradient.
    For vector f: R^n -> R^m, use jacobian() from Section 4 instead.
    """
    x = np.asarray(x); n = len(x)
    y0 = f(mg.tensor(x))
    # 0-d numpy arrays have __len__ but len() raises TypeError
    d = y0.data
    m = len(d) if hasattr(d, '__len__') and d.ndim > 0 else 1
    J = np.zeros((m, n))
    for j in range(n):
        e_j = np.zeros(n); e_j[j] = 1.0
        J[:, j] = jvp_scalar(f, x, e_j)
    return J


def hvp(f, x, v, h=1e-6):
    """Hessian-vector product via finite differences on gradient."""
    x = np.asarray(x).astype(float); v = np.asarray(v).astype(float)
    def grad_fn(xv):
        xt = mg.tensor(xv); xt.requires_grad = True
        y = f(xt); y.backward()
        return np.array(xt.grad)
    return (grad_fn(x + h * v) - grad_fn(x - h * v)) / (2 * h)


# 1.1: JVP scalar
f_sq = lambda x: x ** 3
result = jvp_scalar(f_sq, np.array([2.0]), np.array([0.5]))
check_close(result, 3 * 4 * 0.5, name="1.1 JVP scalar f(x)=x^3")

# 1.2: Jacobian via input seeding (scalar f)
f_sq2 = lambda x: mg.sum(x ** 2)  # f(x) = x0^2 + x1^2, grad = [2x0, 2x1]
J_sq = jacobian_via_seeding(f_sq2, np.array([2.0, 3.0]))
check_close(J_sq, [[4.0, 6.0]], name="1.2 Jacobian scalar f=sum(x^2)")

# 1.3: HVP on non-diagonal Hessian (f = x0*x1, H = [[0,1],[1,0]], Hv = [1,1])
hv_nd = hvp(lambda x: x[0] * x[1], np.array([2.0, 3.0]), np.array([1.0, 1.0]))
check_close(hv_nd, np.array([1.0, 1.0]), tol=1e-4, name="1.3 HVP f=x0*x1")


# ═══════════════════════════════════════════
# SECTION 2: Reverse-Mode Backprop
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print("SECTION 2: Reverse-Mode Backprop")
print("=" * 60)

# 2.1: Basic scalar gradient
x = param(2.0)
y = mg.sin(x ** 2); y.backward()
check_close(float(x.grad), np.cos(4) * 4, name="2.1 Basic gradient sin(x^2)")

# 2.2: Multi-variable gradient
a, b, c = param(3.0), param(4.0), param(1.0)
loss = a * b + mg.exp(c); loss.backward()
check_close(float(a.grad), 4.0, name="2.2a df/da")
check_close(float(b.grad), 3.0, name="2.2b df/db")
check_close(float(c.grad), np.exp(1), name="2.2c df/dc")

# 2.3: Vector output with seed (mygrad 2.3.0 may not populate .grad)
x2 = param(2.0)
y2 = mg.tensor([x2, x2**2, x2**3])
try:
    y2.backward(mg.ones(3))
    check_close(float(x2.grad), 1 + 4 + 12, name="2.3 Vector output seed=ones")
except TypeError:
    test("2.3 Vector output seed (mygrad limitation — .grad is None)", False)

# 2.4: Batch gradient computation
W = param(np.random.randn(10))
X = mg.tensor(np.random.randn(100, 10))
y_true = mg.tensor(np.random.randn(100))
loss = mg.mean((mg.matmul(X, W) - y_true) ** 2)
loss.backward()
test("2.4 Batch gradient shape", W.grad.shape == (10,))

# 2.5: no_autodiff context
with mg.no_autodiff:
    a_nd = mg.tensor(5.0); b_nd = a_nd ** 2
test("2.5 no_autodiff", abs(float(b_nd.data) - 25.0) < 1e-10)

# 2.6: Gradient norm computation
w_clip = param(np.array([10.0, 0.0]))
loss_clip = mg.sum(w_clip ** 2)
loss_clip.backward()
# grad of sum(w^2) = 2*w = [20, 0], norm = 20
norm = np.sqrt(np.sum(np.array(w_clip.grad) ** 2))
check_close(norm, 20.0, name="2.6 Gradient norm computation")

# 2.7: null_grad verification
w_null = param(3.0)
loss_null = (w_null - 1.0) ** 2
loss_null.backward()
test("2.7a grad non-zero before null", abs(float(w_null.grad)) > 1e-10)
w_null.null_grad()
# mygrad 2.3.0: null_grad() sets .grad to None, not zero
test("2.7b grad is None after null_grad", w_null.grad is None)

# 2.8: Gradient accumulation (4 micro-batches sum to full batch)
# Micro-batch MSE has denominator 1/n_micro, full-batch has 1/n_total.
# Sum of micro grads = (n_total/n_micro) * full grad, so divide by 4.
n_total, n_micro = 40, 10
X_full = np.random.randn(n_total, 5)
y_full = np.random.randn(n_total)

# Full batch reference
W_ref = param(np.zeros(5))
Xr = mg.tensor(X_full); yr = mg.tensor(y_full)
loss_ref = mg.mean((mg.matmul(Xr, W_ref) - yr) ** 2)
loss_ref.backward()
ref_grad = np.array(W_ref.grad)

# Accumulated micro-batches — manually sum since .backward() may overwrite
W_acc = param(np.zeros(5))
acc_grad = np.zeros(5)
for start in range(0, n_total, n_micro):
    Xm = mg.tensor(X_full[start:start+n_micro])
    ym = mg.tensor(y_full[start:start+n_micro])
    loss_m = mg.mean((mg.matmul(Xm, W_acc) - ym) ** 2)
    loss_m.backward()
    acc_grad += np.array(W_acc.grad)
    W_acc.null_grad()
# Micro-batch grad has 1/10 scale, full-batch has 1/40 scale
# sum_micro = (4/10) * inner, full = (1/40) * inner, ratio = 16
# Micro-batch MSE: 1/10 * sum, Full-batch MSE: 1/40 * sum. Ratio = 40/10 = 4.
check_close(acc_grad / 4.0, ref_grad, tol=1e-5, name="2.8 Gradient accumulation")


# ═══════════════════════════════════════════
# SECTION 3: Gradient Descent Optimizers
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print("SECTION 3: Gradient Descent Optimizers")
print("=" * 60)


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
        self.velocities = [np.zeros_like(p.data) for p in params]
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
        self.velocities = [np.zeros_like(p.data) for p in params]
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
        self.cache = [np.zeros_like(p.data) for p in params]
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
        self.cache = [np.zeros_like(p.data) for p in params]
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
        self.m = [np.zeros_like(p.data) for p in params]
        self.v = [np.zeros_like(p.data) for p in params]
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
        self.m = [np.zeros_like(p.data) for p in params]
        self.v = [np.zeros_like(p.data) for p in params]
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

class ReduceLROnPlateau:
    def __init__(self, optimizer, factor=0.1, patience=5, min_lr=1e-7):
        self.optimizer, self.factor, self.patience, self.min_lr = optimizer, factor, patience, min_lr
        self.best_loss, self.wait = float('inf'), 0
    def step(self, loss):
        if loss < self.best_loss:
            self.best_loss = loss; self.wait = 0
        else:
            self.wait += 1
            if self.wait >= self.patience:
                self.optimizer.lr = max(self.optimizer.lr * self.factor, self.min_lr)
                self.wait = 0


# 3.1: SGD
w = param(0.0)
opt = SGD([w], lr=0.3)
for _ in range(10): opt.zero_grad(); ((w-3.0)**2).backward(); opt.step()
check_close(float(w.data), 3.0, tol=0.01, name="3.1 SGD")

# 3.2: Momentum
w2 = param(0.0)
opt2 = SGDMomentum([w2], lr=0.1, momentum=0.9)
for _ in range(60): opt2.zero_grad(); ((w2-3.0)**2).backward(); opt2.step()
check_close(float(w2.data), 3.0, tol=0.2, name="3.2 Momentum")

# 3.3: Nesterov
w3 = param(0.0)
opt3 = NesterovSGD([w3], lr=0.1, momentum=0.9)
for _ in range(30): opt3.zero_grad(); ((w3-3.0)**2).backward(); opt3.step()
check_close(float(w3.data), 3.0, tol=0.1, name="3.3 Nesterov")

# 3.4: AdaGrad
w4 = param(0.0)
opt4 = AdaGrad([w4], lr=1.0)
for _ in range(100): opt4.zero_grad(); ((w4-3.0)**2).backward(); opt4.step()
check_close(float(w4.data), 3.0, tol=0.3, name="3.4 AdaGrad")

# 3.5: RMSprop
w5 = param(0.0)
opt5 = RMSprop([w5], lr=0.1, decay=0.9)
for _ in range(30): opt5.zero_grad(); ((w5-3.0)**2).backward(); opt5.step()
check_close(float(w5.data), 3.0, tol=0.3, name="3.5 RMSprop")

# 3.6: Adam
w6 = param(0.0)
opt6 = Adam([w6], lr=0.1)
for _ in range(100): opt6.zero_grad(); ((w6-3.0)**2).backward(); opt6.step()
check_close(float(w6.data), 3.0, tol=0.1, name="3.6 Adam")

# 3.7: AdamW with weight_decay=0.5 pulls toward zero
w7 = param(0.0)
opt7 = AdamW([w7], lr=0.1, weight_decay=0.5)
for _ in range(100): opt7.zero_grad(); ((w7-3.0)**2).backward(); opt7.step()
# Strong weight decay dominates — equilibrium < 1.5
# Equilibrium: Adam gradient ~1.0 (normalized), wd*lr*w = 0.05*w.
# Balance: lr*1.0 = lr*0.5*w -> w=2.0. (100 iters may be near but not at equilibrium.)
check_close(float(w7.data), 2.0, tol=0.5, name="3.7 AdamW wd=0.5 near equilibrium")

# 3.8: AdamW with weight_decay=0 matches Adam exactly
w7b = param(0.0)
opt7b = AdamW([w7b], lr=0.1, weight_decay=0.0)
for _ in range(100): opt7b.zero_grad(); ((w7b-3.0)**2).backward(); opt7b.step()
check_close(float(w7b.data), 3.0, tol=0.1, name="3.8 AdamW wd=0 = Adam")

# 3.9: Cosine annealing
class CosineAnnealingLR:
    def __init__(self, opt, max_epochs=100, min_lr=0.0):
        self.opt, self.max_epochs, self.min_lr, self.base_lr = opt, max_epochs, min_lr, opt.lr
    def step(self, epoch):
        p = epoch / self.max_epochs
        self.opt.lr = self.min_lr + 0.5 * (self.base_lr - self.min_lr) * (1 + np.cos(np.pi * p))

w9 = param(0.0); opt9 = SGD([w9], lr=0.1)
sched = CosineAnnealingLR(opt9, max_epochs=100)
sched.step(50)
check_close(opt9.lr, 0.05, name="3.9 Cosine annealing midway")

# 3.10: StepLR
class StepLR:
    def __init__(self, opt, step_size=30, gamma=0.1):
        self.opt, self.step_size, self.gamma, self.base_lr = opt, step_size, gamma, opt.lr
    def step(self, epoch):
        self.opt.lr = self.base_lr * (self.gamma ** (epoch // self.step_size))

w10 = param(0.0); opt10 = SGD([w10], lr=0.1)
sched10 = StepLR(opt10, step_size=30, gamma=0.1)
sched10.step(35)
check_close(opt10.lr, 0.01, name="3.10 StepLR epoch 35")

# 3.11: ReduceLROnPlateau
w11 = param(0.0); opt11 = SGD([w11], lr=0.1)
sched11 = ReduceLROnPlateau(opt11, factor=0.1, patience=3)
initial_lr = opt11.lr
for _ in range(4):  # 3 plateaus + 1 trigger
    sched11.step(1.0)
check_close(opt11.lr, initial_lr * 0.1, name="3.11 ReduceLROnPlateau drops LR")


# ═══════════════════════════════════════════
# SECTION 4: Higher-Order Derivatives
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print("SECTION 4: Higher-Order Derivatives")
print("=" * 60)

# 4.1: HVP
hv2 = hvp(lambda x: mg.sum(x**3), np.array([1., 2., 3.]),
          np.array([0.5, 0.5, 0.5]))
check_close(hv2, 6 * np.array([1., 2., 3.]) * 0.5, tol=1e-4, name="4.1 HVP sum(x^3)")

# 4.2: Hessian diagonal
def hessian_diagonal(f, x, h=1e-6):
    x = np.asarray(x).astype(float); n = len(x)
    Hd = np.zeros(n)
    def ig(xv, i):
        xt = mg.tensor(xv); xt.requires_grad = True
        y = f(xt); y.backward()
        return float(xt.grad[i])
    for i in range(n):
        ei = np.zeros(n); ei[i] = h
        Hd[i] = (ig(x + ei, i) - ig(x - ei, i)) / (2 * h)
    return Hd

Hd = hessian_diagonal(lambda x: 0.5 * mg.sum(x**2), np.array([1., 2., 3.]))
check_close(Hd, np.array([1., 1., 1.]), tol=1e-4, name="4.2 Hessian diagonal")

# 4.3: Full Hessian
def hessian(f, x, h=1e-6):
    x = np.asarray(x).astype(float); n = len(x)
    H = np.zeros((n, n))
    def ig(xv, i):
        xt = mg.tensor(xv); xt.requires_grad = True
        y = f(xt); y.backward()
        return float(xt.grad[i])
    for i in range(n):
        for j in range(n):
            ej = np.zeros(n); ej[j] = h
            H[i, j] = (ig(x + ej, i) - ig(x - ej, i)) / (2 * h)
    return H

H = hessian(lambda x: 0.5*mg.sum(x**2) + x[0]*x[1], np.array([1., 2.]))
check_close(H, np.array([[1., 1.], [1., 1.]]), tol=1e-4, name="4.3 Full Hessian")

# 4.4: Full Jacobian (vector-valued f with seeded backprop)
def jacobian(f, x):
    """Full Jacobian via seeded backward, with finite-diff fallback.

    Fast path: m seeded backward passes (O(m) evaluations).
    Fallback: central finite differences on f directly (O(2n) evaluations)
    when mygrad 2.3.0 seeded backward returns None.grad on vector outputs.
    """
    x = np.asarray(x).astype(float)
    y0 = f(mg.tensor(x))
    m = len(y0.data) if hasattr(y0.data, '__len__') else 1
    n = len(x); J = np.zeros((m, n))

    # Try seeded backward first (fast path)
    for i in range(m):
        xt = mg.tensor(x); xt.requires_grad = True
        y = f(xt)
        seed = np.zeros(m); seed[i] = 1.0
        y.backward(mg.tensor(seed))
        if xt.grad is None:
            # Fallback to finite differences — always works
            h = 1e-6
            for j in range(n):
                e_j = np.zeros(n); e_j[j] = h
                y_plus = np.array(f(mg.tensor(x + e_j)).data)
                y_minus = np.array(f(mg.tensor(x - e_j)).data)
                J[:, j] = (y_plus - y_minus) / (2 * h)
            return J
        J[i, :] = np.array(xt.grad)
    return J

# 4.4: Full Jacobian (auto-fallback to finite differences on mygrad 2.3.0)
f_jac = lambda x: mg.tensor([x[0] + x[1], x[0] * x[1]])
J = jacobian(f_jac, np.array([2., 3.]))
check_close(J, np.array([[1., 1.], [3., 2.]]), tol=1e-4, name="4.4 Full Jacobian")

# 4.5: Gauss-Newton step (calls jacobian internally — protect against mygrad limitation)
def gauss_newton_step(r, x, damping=1e-6):
    x = np.asarray(x).astype(float)
    J = jacobian(r, x)
    residuals = np.array(r(mg.tensor(x)).data)
    grad = J.T @ residuals
    Hgn = J.T @ J + damping * np.eye(len(x))
    return np.linalg.solve(Hgn, -grad)

r = lambda x: mg.tensor([x[0] - 1.0, x[0] - 2.0])
delta = gauss_newton_step(r, np.array([0.0]))
check_close(delta[0], 1.5, name="4.5 Gauss-Newton step")

# 4.6: Derivatives (orders 0-3)
def derivative(f, x, order=1, h=1e-5):
    x = float(x)
    if order == 0: return float(f(mg.tensor(x)).data)
    if order == 1:
        xt = mg.tensor(x); xt.requires_grad = True
        y = f(xt); y.backward()
        return float(xt.grad)
    if order == 2:
        def ga(t):
            xt = mg.tensor(float(t)); xt.requires_grad = True
            y = f(xt); y.backward()
            return float(xt.grad)
        return (ga(x + h) - ga(x - h)) / (2 * h)
    h3 = h * 10
    def d2a(t): return derivative(f, t, 2, h / 2)
    return (d2a(x + h3) - d2a(x - h3)) / (2 * h3)

fp = lambda x: x ** 4
check_close(derivative(fp, 2.0, 0), 16.0, name="4.6a f(2)")
check_close(derivative(fp, 2.0, 1), 32.0, name="4.6b f'(2)")
check_close(derivative(fp, 2.0, 2), 48.0, tol=0.01, name="4.6c f''(2)")
check_close(derivative(fp, 2.0, 3), 48.0, tol=5.0, name="4.6d f'''(2)")

# 4.7: Newton-CG step
def newton_step_cg(f, x, max_cg_iters=50, tol=1e-6, h=1e-6):
    x = np.asarray(x).astype(float); n = len(x)
    xt = mg.tensor(x); xt.requires_grad = True
    y = f(xt); y.backward()
    grad = np.array(xt.grad)
    d, r, p = np.zeros(n), -grad.copy(), -grad.copy()
    rr = np.dot(r, r)
    for _ in range(max_cg_iters):
        Hp = hvp(f, x, p, h=h)
        pHp = np.dot(p, Hp)
        if abs(pHp) < 1e-12: break
        alpha = rr / pHp; d += alpha * p; r -= alpha * Hp
        nr = np.dot(r, r)
        if np.sqrt(nr) < tol: break
        p = r + (nr / rr) * p; rr = nr
    return d

dn = newton_step_cg(lambda x: 0.5 * mg.sum((x - 3.0)**2), np.array([0.0]))
check_close(dn[0], 3.0, tol=0.01, name="4.7 Newton-CG step")


# ═══════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print(f"RESULTS: {PASSED}/{PASSED + FAILED} passed, {FAILED} failed")
print("=" * 60)

if __name__ == "__main__":
    if FAILED > 0:
        print(f"\n{FAILED} TEST(S) FAILED")
        sys.exit(1)
    else:
        print("\nALL TESTS PASSED")
        sys.exit(0)
