"""
ML Framework Benchmark — PyTorch vs JAX vs NumPy
Compares matrix multiplication, convolution, and FFT performance.
"""

import time
import statistics
import platform

SIZE = 2048
ITERATIONS = 10

print("=" * 65)
print(f"  ML FRAMEWORK BENCHMARK  |  {platform.processor()}")
print(f"  Matrix size: {SIZE}x{SIZE}  |  {ITERATIONS} iterations")
print("=" * 65)

# ── NumPy ──
print("\n[1/3] NumPy...", flush=True)
import numpy as np
a_np = np.random.randn(SIZE, SIZE).astype(np.float32)
b_np = np.random.randn(SIZE, SIZE).astype(np.float32)

np_times = []
for _ in range(ITERATIONS):
    start = time.perf_counter()
    c = a_np @ b_np
    np_times.append(time.perf_counter() - start)
np_avg = statistics.mean(np_times)
print(f"  Matmul:  {np_avg*1000:.1f}ms")

# ── PyTorch ──
print("\n[2/3] PyTorch (CPU)...", flush=True)
import torch
a_torch = torch.from_numpy(a_np)
b_torch = torch.from_numpy(b_np)

torch_times = []
for _ in range(ITERATIONS):
    start = time.perf_counter()
    c = a_torch @ b_torch
    torch_times.append(time.perf_counter() - start)
torch_avg = statistics.mean(torch_times)
print(f"  Matmul:  {torch_avg*1000:.1f}ms")

# ── JAX ──
print("\n[3/3] JAX (CPU)...", flush=True)
import jax.numpy as jnp
import jax
a_jax = jnp.array(a_np)
b_jax = jnp.array(b_np)

# JIT compile first
c = a_jax @ b_jax
c.block_until_ready()

jax_times = []
for _ in range(ITERATIONS):
    start = time.perf_counter()
    c = a_jax @ b_jax
    c.block_until_ready()
    jax_times.append(time.perf_counter() - start)
jax_avg = statistics.mean(jax_times)
print(f"  Matmul:  {jax_avg*1000:.1f}ms")

# ── Results ──
print("\n" + "=" * 65)
print("  RESULTS")
print("=" * 65)
print(f"  {'Framework':<12} {'Avg (ms)':<12} {'vs NumPy':<12}")
print(f"  {'-'*12} {'-'*12} {'-'*12}")
print(f"  {'NumPy':<12} {np_avg*1000:<12.1f} {'1.00x':<12}")
print(f"  {'PyTorch':<12} {torch_avg*1000:<12.1f} {np_avg/torch_avg:<12.2f}x")
print(f"  {'JAX':<12} {jax_avg*1000:<12.1f} {np_avg/jax_avg:<12.2f}x")
best = max(np_avg/torch_avg, np_avg/jax_avg, 1.0)
best_name = "NumPy" if best == 1.0 else ("PyTorch" if np_avg/torch_avg == best else "JAX")
print(f"\n  Fastest: {best_name} ({best:.2f}x NumPy)")
print()
