#!/usr/bin/env python
# coding: utf-8
"""
benchmark_autodiff_modes.py — Forward vs Reverse Autodiff Benchmark for mygrad 2.3.0

Compares autodiff modes on Python 3.14t free-threading:
  1. Reverse (O(1) .backward()) vs Forward-emulated (O(n) backprops)
  2. Topology wins: reverse wins R^n->R, forward wins R^1->R^m
  3. Multi-threaded free-threading scaling (sequential vs 1/2/4/8 threads)

Usage:
  python tests/benchmark_autodiff_modes.py
  python tests/benchmark_autodiff_modes.py --quick   (fewer tasks, faster)
"""

import sys
import os
import time
import concurrent.futures
import sysconfig

# mygrad -> numba, whose cp314t extension faults at C level in a
# free-threaded process; a segfault cannot be caught, so check the runtime
# before the import rather than wrapping it in try/except.
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    print("SKIP: mygrad -> numba cannot be imported in a free-threaded build.")
    print("      Re-run under the GIL interpreter: python tests/benchmark_autodiff_modes.py")
    sys.exit(0)

import mygrad as mg
import numpy as np


# ═══════════════════════════════════════════════════════════
# TOPOLOGIES
# ═══════════════════════════════════════════════════════════

def f_Rn_to_R(x):
    """f: R^n -> R — scalar loss (reverse-mode wins, O(1) vs O(n))."""
    return mg.sum(x ** 2)


def f_R1_to_Rm(x):
    """f: R^1 -> R^m — vector output (forward-mode wins, O(1) vs O(m))."""
    # Expand single input to m=500 outputs via learnable weight
    w = mg.tensor(np.ones(500))
    return x * w


# ═══════════════════════════════════════════════════════════
# AUTODIFF MODES (mygrad 2.3.0 compatible)
# ═══════════════════════════════════════════════════════════

def reverse_gradient(x_val):
    """Reverse mode: single .backward() call — O(1) for scalar f."""
    x = mg.tensor(np.asarray(x_val).astype(float))
    x.requires_grad = True
    y = f_Rn_to_R(x)
    y.backward()
    return np.array(x.grad).copy()


def forward_emulated_gradient(x_val):
    """Emulated forward mode: n .backward() calls — O(n) for scalar f.

    Extracts each gradient component via repeated full backward passes.
    Inefficient by design — demonstrates why reverse mode wins for R^n->R.
    """
    x_val = np.asarray(x_val).astype(float)
    n = len(x_val)
    grad = np.zeros(n)
    for i in range(n):
        x = mg.tensor(x_val)
        x.requires_grad = True
        y = f_Rn_to_R(x)
        y.backward()
        grad[i] = float(x.grad[i])
    return grad


def reverse_jacobian(x_val):
    """Reverse mode for R^1 -> R^m: m seeded backward passes — O(m).

    Each pass seeds one output component, building Jacobian row by row.
    Demonstrates why reverse mode is SLOW for many-output functions.
    """
    m = 500
    jac = np.zeros(m)
    for i in range(m):
        x = mg.tensor(np.array([float(x_val)]))
        x.requires_grad = True
        y = f_R1_to_Rm(x)
        seed = np.zeros(m)
        seed[i] = 1.0
        y.backward(mg.tensor(seed))
        if x.grad is not None:
            jac[i] = float(x.grad[0])
    return jac


def forward_jvp(x_val):
    """Forward-mode JVP for R^1 -> R^m: directional derivative O(1).

    Seeds all outputs with ones to compute sum(Jacobian) = J^T @ ones.
    This is a directional derivative, NOT the full Jacobian.
    Useful when you only need sensitivity in a specific direction.
    """
    x = mg.tensor(np.array([float(x_val)]))
    x.requires_grad = True
    y = f_R1_to_Rm(x)
    m = len(y.data) if hasattr(y.data, '__len__') else 1
    y.backward(mg.ones(m))
    if x.grad is None:
        raise RuntimeError("mygrad 2.3.0: backward on vector output returned None.grad")
    return np.array(x.grad).copy()


# ═══════════════════════════════════════════════════════════
# PARALLEL EXECUTION (3.14t Free-Threading)
# ═══════════════════════════════════════════════════════════

def run_parallel(func, inputs, max_workers, progress=False):
    """Run func on inputs using ThreadPoolExecutor."""
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(func, inputs))
    elapsed = time.perf_counter() - t0
    return elapsed, results


def run_sequential(func, inputs):
    """Run func on inputs sequentially."""
    t0 = time.perf_counter()
    results = [func(x) for x in inputs]
    elapsed = time.perf_counter() - t0
    return elapsed, results


# ═══════════════════════════════════════════════════════════
# BENCHMARKS
# ═══════════════════════════════════════════════════════════

def bench_reverse_wins(n_dim=100, n_tasks=200):
    """R^n -> R: reverse (1 pass) vs forward-emulated (n passes)."""
    inputs = [np.random.randn(n_dim) for _ in range(n_tasks)]

    t_rev, _ = run_sequential(reverse_gradient, inputs)
    t_fwd, _ = run_sequential(forward_emulated_gradient, inputs)

    return {
        'topology': f'R^{n_dim} -> R (scalar loss)',
        'reverse_s': t_rev,
        'forward_s': t_fwd,
        'speedup': t_fwd / t_rev if t_rev > 0 else 0,
        'winner': 'REVERSE',
        'n_tasks': n_tasks,
        'n_dim': n_dim,
    }


def bench_forward_mode_jvp(n_tasks=100):
    """R^1 -> R^500: JVP (directional, O(1)) vs Full Jacobian (O(m)).

    NOT a forward-vs-reverse comparison — both use reverse-mode in mygrad.
    Shows the cost difference between computing a cheap directional
    derivative (JVP, 1 pass) vs the full Jacobian (m passes).
    """
    inputs = [np.random.randn() for _ in range(n_tasks)]

    t_jvp, _ = run_sequential(forward_jvp, inputs)
    t_jac, _ = run_sequential(reverse_jacobian, inputs)

    return {
        'topology': 'R^1 -> R^500 (vector output)',
        'jvp_s': t_jvp,
        'jacobian_s': t_jac,
        'speedup': t_jac / t_jvp if t_jvp > 0 else 0,
        'note': 'JVP (directional) vs Full Jacobian — not equivalent computations',
        'n_tasks': n_tasks,
    }


def bench_threading(n_dim=100, n_tasks=400, workers_list=None):
    """Free-threading scaling: multi-threaded reverse gradient."""
    if workers_list is None:
        workers_list = [1, 2, 4, 8]
    inputs = [np.random.randn(n_dim) for _ in range(n_tasks)]

    results = {}
    for w in workers_list:
        t, _ = run_parallel(reverse_gradient, inputs, max_workers=w)
        results[w] = t

    baseline = results[1] if results else 1.0
    scaling = {w: baseline / results[w] for w in workers_list if results[w] > 0}

    return {
        'topology': f'R^{n_dim} -> R (parallel reverse)',
        'n_tasks': n_tasks,
        'times_s': results,
        'speedup_vs_1thread': scaling,
    }


# ═══════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════

def hr(title):
    print(f"\n{'=' * 65}")
    print(f"  {title}")
    print(f"{'=' * 65}")


def main():
    quick = '--quick' in sys.argv

    if quick:
        n_tasks_rev = 50
        n_tasks_fwd = 20
        n_dim = 50
        n_tasks_thread = 100
    else:
        n_tasks_rev = 200
        n_tasks_fwd = 100
        n_dim = 100
        n_tasks_thread = 400

    # ── FREE-THREADING DETECTION ──
    hr("Python 3.14t Free-Threading Environment")
    try:
        import sysconfig
        abi = sysconfig.get_config_var('ABIFLAGS') or '(none)'
        is_ft = 't' in abi
        print(f"  ABI flags:       {abi}")
        print(f"  Free-threading:  {'YES (no GIL)' if is_ft else 'NO (standard GIL)'}")
    except Exception:
        print(f"  Free-threading:  unable to detect")
    print(f"  CPU cores:       {os.cpu_count() or '?'}")
    print(f"  Python:          {sys.version.split()[0]}")

    # ── BENCHMARK 1: Reverse Wins (R^n -> R) ──
    hr(f"BENCHMARK 1: Reverse Wins — f: R^{n_dim} -> R (scalar loss)")
    print(f"  Reverse: 1 .backward() call per task")
    print(f"  Forward: {n_dim} .backward() calls per task (emulated)")
    print(f"  Tasks: {n_tasks_rev}")
    print()

    r1 = bench_reverse_wins(n_dim=n_dim, n_tasks=n_tasks_rev)
    print(f"  Reverse mode:  {r1['reverse_s']:>8.3f}s  (O(1) passes)")
    print(f"  Forward mode:  {r1['forward_s']:>8.3f}s  (O({n_dim}) passes)")
    print(f"  Speedup:       {r1['speedup']:>8.1f}x  ({r1['winner']} wins)")
    print(f"  Per-task rev:  {r1['reverse_s']/n_tasks_rev*1000:.2f}ms")
    print(f"  Per-task fwd:  {r1['forward_s']/n_tasks_rev*1000:.2f}ms")

    # ── BENCHMARK 2: JVP vs Full Jacobian ──
    m = 500
    hr(f"BENCHMARK 2: JVP (directional) vs Full Jacobian — f: R^1 -> R^{m}")
    print(f"  JVP (directional derivative): 1 backward pass (O(1))")
    print(f"  Full Jacobian: {m} seeded backward passes (O({m}))")
    print(f"  Note: both use reverse-mode in mygrad, not true forward-mode")
    print(f"  Tasks: {n_tasks_fwd}")
    print()

    r2 = bench_forward_mode_jvp(n_tasks=n_tasks_fwd)
    print(f"  JVP (directional):  {r2['jvp_s']:>8.3f}s  (O(1) pass)")
    print(f"  Full Jacobian:      {r2['jacobian_s']:>8.3f}s  (O({m}) passes)")
    print(f"  Ratio:              {r2['speedup']:>8.1f}x  (JVP is {r2['speedup']:.0f}x cheaper)")
    print(f"  Use JVP when you only need sensitivity in a specific direction;")
    print(f"  use Full Jacobian when you need all output sensitivities.")

    # ── BENCHMARK 3: Free-Threading Scaling ──
    workers = [1, 2, 4, 8]
    hr(f"BENCHMARK 3: Free-Threading Scaling (reverse mode, {n_dim}-dim)")
    print(f"  {n_tasks_thread} independent gradient computations")
    print(f"  ThreadPoolExecutor with {workers} worker counts")
    print()

    r3 = bench_threading(n_dim=n_dim, n_tasks=n_tasks_thread, workers_list=workers)

    # True sequential baseline (no thread overhead)
    seq_inputs = [np.random.randn(n_dim) for _ in range(n_tasks_thread)]
    t_seq, _ = run_sequential(reverse_gradient, seq_inputs)
    baseline = t_seq
    print(f"  Sequential (no threads):  {t_seq:>8.3f}s  (baseline)")
    for w in workers:
        t = r3['times_s'].get(w, 0)
        speedup = baseline / t if t > 0 else 0
        ideal = w
        efficiency = speedup / ideal * 100 if ideal > 0 else 0
        bar = '#' * int(speedup * 4) if speedup > 0 else ''
        print(f"  {w} thread(s):  {t:>8.3f}s  |  {speedup:>4.1f}x speedup  "
              f"({efficiency:>5.0f}% efficient vs ideal {ideal}x)  {bar}")

    if len(r3['speedup_vs_1thread']) > 1:
        max_speedup = max(r3['speedup_vs_1thread'].values())
        if max_speedup > 1.5:
            print(f"\n  >> Free-threading scaling observed (max {max_speedup:.1f}x)")
        else:
            print(f"\n  >> No significant free-threading scaling detected")

    # ── BENCHMARK 4: Throughput Summary ──
    hr("BENCHMARK 4: Throughput Summary")
    print(f"  Reverse gradient (R^{n_dim}->R):  "
          f"{n_tasks_rev/r1['reverse_s']:.0f} tasks/s  "
          f"({n_dim*n_tasks_rev/r1['reverse_s']:.0f} dims/s)")
    print(f"  JVP (R^1->R^{m}):              "
          f"{n_tasks_fwd/r2['jvp_s']:.0f} tasks/s")
    print()

    # ── RECOMMENDATIONS ──
    hr("MODE SELECTION GUIDE")
    print(f"  f: R^n -> R (scalar loss)     >> REVERSE mode  (O(1) .backward())")
    print(f"  f: R^1 -> R^m (vector output) >> FORWARD mode   (O(1) seeded backward)")
    print(f"  Directional derivative         >> REVERSE mode  (dot(grad, v))")
    print(f"  HVP / Hessian                  >> finite-diff on gradient (mygrad limit)")
    print(f"  Gradient at scale              >> REVERSE + parallel threads")
    print()

    return 0


if __name__ == '__main__':
    main()
