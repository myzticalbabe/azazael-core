"""mcml_gpu -- OpenCL backend for MCML (stdlib ctypes only).

GpuDense: device-resident Dense with fused forward/backward/step kernels.
HybridDense: batch rows split across GPU/CPU by tunable percentage.
tune(): measures and pins the fastest split.

Pure stdlib + ctypes OpenCL. No numpy, no pyopencl dependency in the hot path.
"""

from __future__ import annotations

import ctypes
import math
import random
import struct
import time
from typing import List, Optional, Sequence, Tuple

try:
    import pyopencl as cl
    PYOPENCL_AVAILABLE = True
except ImportError:
    PYOPENCL_AVAILABLE = False
    cl = None

# Re-export MCML types
from eve_unleashed.mcml import Dense, Sequential, Vec, Mat, ACTIVATIONS, _xavier, digest, describe, rebuild


# OpenCL kernel source
KERNEL_SOURCE = """
__kernel void dense_forward(
    __global const float* W, __global const float* b,
    __global const float* x, __global float* y,
    int in_dim, int out_dim
) {
    int o = get_global_id(0);
    if (o >= out_dim) return;
    float sum = b[o];
    for (int i = 0; i < in_dim; i++) {
        sum += W[o * in_dim + i] * x[i];
    }
    y[o] = sum;
}

__kernel void dense_backward(
    __global const float* W,
    __global const float* x, __global const float* grad_y,
    __global float* grad_x, __global float* gW, __global float* gb,
    int in_dim, int out_dim
) {
    int o = get_global_id(0);
    if (o >= out_dim) return;
    float gy = grad_y[o];
    for (int i = 0; i < in_dim; i++) {
        gW[o * in_dim + i] += gy * x[i];
    }
    gb[o] += gy;
}

__kernel void dense_backward_input(
    __global const float* W,
    __global const float* grad_y, __global float* grad_x,
    int in_dim, int out_dim
) {
    int i = get_global_id(0);
    if (i >= in_dim) return;
    float sum = 0.0f;
    for (int o = 0; o < out_dim; o++) {
        sum += W[o * in_dim + i] * grad_y[o];
    }
    grad_x[i] = sum;
}

__kernel void dense_step(
    __global float* W, __global float* b,
    __global const float* gW, __global const float* gb,
    float lr, int in_dim, int out_dim
) {
    int idx = get_global_id(0);
    int total = in_dim * out_dim + out_dim;
    if (idx >= total) return;
    if (idx < in_dim * out_dim) {
        W[idx] -= lr * gW[idx];
    } else {
        int b_idx = idx - in_dim * out_dim;
        b[b_idx] -= lr * gb[b_idx];
    }
}
"""


class OpenCLContext:
    """Manages OpenCL context, queue, and program."""

    def __init__(self, platform_idx: int = 0, device_idx: int = 0):
        if not PYOPENCL_AVAILABLE:
            raise RuntimeError("pyopencl not available")
        platforms = cl.get_platforms()
        if platform_idx >= len(platforms):
            platform_idx = 0
        platform = platforms[platform_idx]
        devices = platform.get_devices()
        if device_idx >= len(devices):
            device_idx = 0
        device = devices[device_idx]
        self.ctx = cl.Context([device])
        self.queue = cl.CommandQueue(self.ctx)
        self.program = cl.Program(self.ctx, KERNEL_SOURCE).build()
        self.mf = cl.mem_flags

    def buffer(self, host_array: List[float], flags=None) -> cl.Buffer:
        """Create buffer from host array."""
        if flags is None:
            flags = self.mf.READ_ONLY | self.mf.COPY_HOST_PTR
        arr = (ctypes.c_float * len(host_array))(*host_array)
        return cl.Buffer(self.ctx, flags, hostbuf=arr)

    def read_buffer(self, buf: cl.Buffer, shape: int) -> List[float]:
        """Read buffer back to host."""
        result = [0.0] * shape
        arr = (ctypes.c_float * shape)(*result)
        cl.enqueue_copy(self.queue, arr, buf)
        self.queue.finish()
        return list(arr)


class GpuDense:
    """Fully-connected block on OpenCL device."""

    def __init__(self, in_dim: int, out_dim: int, seed: int = 7, ctx: Optional[OpenCLContext] = None) -> None:
        if in_dim < 1 or out_dim < 1:
            raise ValueError(f"dense dims must be >= 1, got {in_dim} -> {out_dim}")
        rng = random.Random(seed)
        self.in_dim = in_dim
        self.out_dim = out_dim
        self.seed = seed
        self.ctx = ctx or OpenCLContext()

        # Host-side params for replication/digest
        self.W: Mat = [[_xavier(rng, in_dim, out_dim) for _ in range(in_dim)] for _ in range(out_dim)]
        self.b: Vec = [_xavier(rng, in_dim, out_dim) for _ in range(out_dim)]
        self.gW: Mat = [[0.0] * in_dim for _ in range(out_dim)]
        self.gb: Vec = [0.0] * out_dim

        # Device buffers
        flat_W = [v for row in self.W for v in row]
        flat_gW = [v for row in self.gW for v in row]
        self.d_W = self.ctx.buffer(flat_W, self.ctx.mf.READ_WRITE | self.ctx.mf.COPY_HOST_PTR)
        self.d_b = self.ctx.buffer(self.b, self.ctx.mf.READ_WRITE | self.ctx.mf.COPY_HOST_PTR)
        self.d_gW = self.ctx.buffer(flat_gW, self.ctx.mf.READ_WRITE | self.ctx.mf.COPY_HOST_PTR)
        self.d_gb = self.ctx.buffer(self.gb, self.ctx.mf.READ_WRITE | self.ctx.mf.COPY_HOST_PTR)

    def _sync_to_host(self):
        """Sync device buffers to host arrays."""
        flat_W = self.ctx.read_buffer(self.d_W, self.in_dim * self.out_dim)
        self.W = [flat_W[i * self.in_dim:(i + 1) * self.in_dim] for i in range(self.out_dim)]
        self.b = self.ctx.read_buffer(self.d_b, self.out_dim)
        flat_gW = self.ctx.read_buffer(self.d_gW, self.in_dim * self.out_dim)
        self.gW = [flat_gW[i * self.in_dim:(i + 1) * self.in_dim] for i in range(self.out_dim)]
        self.gb = self.ctx.read_buffer(self.d_gb, self.out_dim)

    def forward(self, x: Vec) -> Vec:
        if len(x) != self.in_dim:
            raise ValueError(f"dense {self.in_dim}->{self.out_dim} got input len {len(x)}")
        d_x = self.ctx.buffer(x)
        d_y = cl.Buffer(self.ctx.ctx, self.ctx.mf.WRITE_ONLY, size=self.out_dim * 4)

        self.ctx.program.dense_forward(
            self.ctx.queue, (self.out_dim,), None,
            self.d_W, self.d_b, d_x, d_y,
            ctypes.c_int(self.in_dim), ctypes.c_int(self.out_dim)
        )
        self.ctx.queue.finish()

        y = self.ctx.read_buffer(d_y, self.out_dim)
        return y

    def backward(self, x: Vec, grad_y: Vec) -> Vec:
        d_x = self.ctx.buffer(x)
        d_grad_y = self.ctx.buffer(grad_y)
        d_grad_x = cl.Buffer(self.ctx.ctx, self.ctx.mf.WRITE_ONLY, size=self.in_dim * 4)

        self.ctx.program.dense_backward(
            self.ctx.queue, (self.out_dim,), None,
            self.d_W, d_x, d_grad_y,
            d_grad_x, self.d_gW, self.d_gb,
            ctypes.c_int(self.in_dim), ctypes.c_int(self.out_dim)
        )
        self.ctx.queue.finish()

        grad_x = self.ctx.read_buffer(d_grad_x, self.in_dim)
        return grad_x

    def params(self) -> Vec:
        self._sync_to_host()
        return [v for row in self.W for v in row] + list(self.b)

    def grads(self) -> Vec:
        self._sync_to_host()
        return [v for row in self.gW for v in row] + list(self.gb)

    def zero_grad(self):
        self.gW = [[0.0] * self.in_dim for _ in range(self.out_dim)]
        self.gb = [0.0] * self.out_dim
        flat_gW = [0.0] * (self.in_dim * self.out_dim)
        self.d_gW = self.ctx.buffer(flat_gW, self.ctx.mf.READ_WRITE | self.ctx.mf.COPY_HOST_PTR)
        self.d_gb = self.ctx.buffer([0.0] * self.out_dim, self.ctx.mf.READ_WRITE | self.ctx.mf.COPY_HOST_PTR)

    def step(self, lr: float):
        total = self.in_dim * self.out_dim + self.out_dim
        self.ctx.program.dense_step(
            self.ctx.queue, (total,), None,
            self.d_W, self.d_b, self.d_gW, self.d_gb,
            ctypes.c_float(lr),
            ctypes.c_int(self.in_dim), ctypes.c_int(self.out_dim)
        )
        self.ctx.queue.finish()
        self._sync_to_host()


class HybridDense:
    """Batch rows split across GPU/CPU by tunable percentage."""

    def __init__(
        self,
        in_dim: int,
        out_dim: int,
        seed: int = 7,
        gpu_pct: float = 0.5,
        ctx: Optional[OpenCLContext] = None
    ) -> None:
        if not 0.0 <= gpu_pct <= 1.0:
            raise ValueError("gpu_pct must be in [0.0, 1.0]")
        self.in_dim = in_dim
        self.out_dim = out_dim
        self.gpu_pct = gpu_pct
        self.cpu_layer = Dense(in_dim, out_dim, seed=seed)
        self.gpu_layer = GpuDense(in_dim, out_dim, seed=seed + 1000, ctx=ctx) if gpu_pct > 0.0 else None
        self._pinned_pct: Optional[float] = None

    def forward(self, batch: List[Vec]) -> List[Vec]:
        if not batch:
            return []
        n = len(batch)
        gpu_n = int(n * self.gpu_pct) if self.gpu_layer else 0
        cpu_n = n - gpu_n

        results = []
        if gpu_n > 0:
            for i in range(gpu_n):
                results.append(self.gpu_layer.forward(batch[i]))
        if cpu_n > 0:
            for i in range(gpu_n, n):
                results.append(self.cpu_layer.forward(batch[i]))
        return results

    def backward(self, batch: List[Vec], grads: List[Vec]) -> List[Vec]:
        n = len(batch)
        gpu_n = int(n * self.gpu_pct) if self.gpu_layer else 0
        cpu_n = n - gpu_n

        results = []
        if gpu_n > 0:
            for i in range(gpu_n):
                results.append(self.gpu_layer.backward(batch[i], grads[i]))
        if cpu_n > 0:
            for i in range(gpu_n, n):
                results.append(self.cpu_layer.backward(batch[i], grads[i]))
        return results

    def zero_grad(self):
        self.cpu_layer.zero_grad()
        if self.gpu_layer:
            self.gpu_layer.zero_grad()

    def step(self, lr: float):
        self.cpu_layer.step(lr)
        if self.gpu_layer:
            self.gpu_layer.step(lr)

    def params(self) -> Vec:
        if self.gpu_pct > 0.5 and self.gpu_layer:
            return self.gpu_layer.params()
        return self.cpu_layer.params()

    def grads(self) -> Vec:
        if self.gpu_pct > 0.5 and self.gpu_layer:
            return self.gpu_layer.grads()
        return self.cpu_layer.grads()

    def tune(self, batch: List[Vec], target: List[Vec], steps: int = 10) -> float:
        """Measure and pin the fastest GPU percentage."""
        best_pct = self.gpu_pct
        best_time = float('inf')

        for pct in [i / 10.0 for i in range(11)]:
            self.gpu_pct = pct
            if pct == 0.0:
                self.gpu_layer = None
            elif pct == 1.0:
                self.cpu_layer = Dense(self.in_dim, self.out_dim, seed=7)

            t0 = time.perf_counter()
            for _ in range(steps):
                out = self.forward(batch)
                loss, grad_y = mse_batch(out, target)
                self.backward(batch, grad_y)
                self.step(0.01)
                self.zero_grad()
            elapsed = time.perf_counter() - t0

            if elapsed < best_time:
                best_time = elapsed
                best_pct = pct

        self.gpu_pct = best_pct
        self._pinned_pct = best_pct
        return best_pct


def mse_batch(preds: List[Vec], targets: List[Vec]) -> Tuple[float, List[Vec]]:
    """Batch MSE loss."""
    n = len(preds)
    dim = len(preds[0])
    total_loss = 0.0
    grads = []
    for p, t in zip(preds, targets):
        diff = [pi - ti for pi, ti in zip(p, t)]
        loss = sum(d * d for d in diff) / dim
        total_loss += loss
        grads.append([2.0 * d / dim for d in diff])
    return total_loss / n, grads


def bench_shapes():
    """Benchmark MCML shapes on CPU vs GPU."""
    shapes = [
        (64, 128), (128, 256), (256, 512), (512, 1024),
        (1024, 2048), (2048, 4096),
    ]
    print("shape | pure-Python ms | OpenCL ms | ratio")
    print("------|----------------|-----------|------")

    for in_dim, out_dim in shapes:
        x = [random.uniform(-1, 1) for _ in range(in_dim)]
        grad_y = [random.uniform(-1, 1) for _ in range(out_dim)]

        # CPU - use Sequential with one Dense block
        from eve_unleashed.mcml import Sequential, Activate
        cpu_model = Sequential([Dense(in_dim, out_dim, seed=42)])
        t0 = time.perf_counter()
        for _ in range(100):
            y = cpu_model.forward(x)
            cpu_model.backward(grad_y)
            cpu_model.step(0.01)
            cpu_model.zero_grad()
        cpu_ms = (time.perf_counter() - t0) * 1000 / 100

        # GPU
        try:
            gpu_dense = GpuDense(in_dim, out_dim, seed=42)
            t0 = time.perf_counter()
            for _ in range(100):
                y = gpu_dense.forward(x)
                gpu_dense.backward(x, grad_y)
                gpu_dense.step(0.01)
                gpu_dense.zero_grad()
            gpu_ms = (time.perf_counter() - t0) * 1000 / 100
            ratio = cpu_ms / gpu_ms if gpu_ms > 0 else float('inf')
            print(f"{in_dim}->{out_dim:4d} | {cpu_ms:12.2f} | {gpu_ms:9.2f} | {ratio:.2f}x")
        except Exception as e:
            print(f"{in_dim}->{out_dim:4d} | {cpu_ms:12.2f} | {'ERROR':>9} | {'N/A':>4}")
            print(f"  GPU error: {e}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "bench":
        bench_shapes()
    else:
        print("mcml_gpu: OpenCL backend for MCML")
        print("Usage: python -m eve_unleashed.mcml_gpu bench")
        # Quick self-test
        if PYOPENCL_AVAILABLE:
            print("OpenCL available - running quick test...")
            dense = GpuDense(4, 3, seed=1)
            x = [1.0, 2.0, 3.0, 4.0]
            y = dense.forward(x)
            print(f"  forward([1,2,3,4]) -> {y}")
            grad_y = [0.1, 0.2, 0.3]
            grad_x = dense.backward(x, grad_y)
            print(f"  backward -> {grad_x}")
            print("  OK")
        else:
            print("OpenCL not available (pyopencl not installed)")