"""Microcode dispatch - feature to kernel mapping for Python 3.16t free-threaded.

Maps detected CPU features (from CPUID) to optimal kernel implementations
for numerical computing, cryptography, and other performance-critical paths.
"""

import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple

from CORE.cpuid import CPUFeatures, get_cpuid


class KernelBackend(Enum):
    """Available kernel backends."""
    REFERENCE = "reference"      # Pure Python reference
    SCALAR = "scalar"            # Optimized scalar (SSE2 baseline)
    SSE42 = "sse42"              # SSE4.2 optimized
    AVX = "avx"                  # AVX optimized
    AVX2 = "avx2"                # AVX2 optimized (256-bit)
    AVX512F = "avx512f"          # AVX-512 Foundation (512-bit)
    AVX512VL = "avx512vl"        # AVX-512 Vector Length
    AVX512BW = "avx512bw"        # AVX-512 Byte/Word
    AVX512DQ = "avx512dq"        # AVX-512 Double/Quad
    AVX512VNNI = "avx512vnni"    # AVX-512 VNNI (INT8 dot product)
    AVX512BF16 = "avx512bf16"    # AVX-512 BF16
    AMX_TILE = "amx_tile"        # AMX Tile (1024-bit)
    AMX_INT8 = "amx_int8"        # AMX INT8
    AMX_BF16 = "amx_bf16"        # AMX BF16
    AMX_FP16 = "amx_fp16"        # AMX FP16
    NEON = "neon"                # ARM NEON (for cross-compile reference)
    SVE = "sve"                  # ARM SVE (for cross-compile reference)


class WorkloadType(Enum):
    """Types of workloads for dispatch."""
    GEMM = "gemm"                    # General matrix multiply
    CONVOLUTION = "convolution"      # Convolution operations
    DOT_PRODUCT = "dot_product"      # Vector dot products
    REDUCTION = "reduction"          # Reduction operations
    ELEMENTWISE = "elementwise"      # Element-wise operations
    FFT = "fft"                      # Fast Fourier Transform
    SORT = "sort"                    # Sorting
    CRYPTO_AES = "crypto_aes"        # AES encryption
    CRYPTO_SHA = "crypto_sha"        # SHA hashing
    RANDOM = "random"                # Random number generation
    STRING = "string"                # String operations
    COMPRESSION = "compression"      # Compression/decompression


@dataclass(frozen=True)
class DispatchRule:
    """Rule for dispatching to a kernel backend."""
    workload: WorkloadType
    required_features: Set[str]
    preferred_backend: KernelBackend
    fallback_backend: KernelBackend
    min_vector_width: int = 0  # Minimum vector width in bits
    description: str = ""


@dataclass
class KernelDispatcher:
    """Dispatches workloads to optimal kernel backends based on CPU features."""
    features: CPUFeatures
    rules: List[DispatchRule] = field(default_factory=list)
    _cache: Dict[WorkloadType, KernelBackend] = field(default_factory=dict)

    def __post_init__(self):
        self._build_default_rules()

    def _build_default_rules(self) -> None:
        """Build default dispatch rules based on CPU features."""
        self.rules = [
            # GEMM rules
            DispatchRule(
                workload=WorkloadType.GEMM,
                required_features={"avx512f", "avx512vl", "avx512bw", "avx512dq"},
                preferred_backend=KernelBackend.AVX512F,
                fallback_backend=KernelBackend.AVX2,
                min_vector_width=512,
                description="AVX-512 GEMM with full feature set"
            ),
            DispatchRule(
                workload=WorkloadType.GEMM,
                required_features={"avx512f"},
                preferred_backend=KernelBackend.AVX512F,
                fallback_backend=KernelBackend.AVX2,
                min_vector_width=512,
                description="AVX-512 Foundation GEMM"
            ),
            DispatchRule(
                workload=WorkloadType.GEMM,
                required_features={"avx2"},
                preferred_backend=KernelBackend.AVX2,
                fallback_backend=KernelBackend.AVX,
                min_vector_width=256,
                description="AVX2 GEMM"
            ),
            DispatchRule(
                workload=WorkloadType.GEMM,
                required_features={"avx"},
                preferred_backend=KernelBackend.AVX,
                fallback_backend=KernelBackend.SSE42,
                min_vector_width=256,
                description="AVX GEMM"
            ),
            DispatchRule(
                workload=WorkloadType.GEMM,
                required_features={"sse4_2"},
                preferred_backend=KernelBackend.SSE42,
                fallback_backend=KernelBackend.SCALAR,
                min_vector_width=128,
                description="SSE4.2 GEMM"
            ),
            DispatchRule(
                workload=WorkloadType.GEMM,
                required_features=set(),
                preferred_backend=KernelBackend.SCALAR,
                fallback_backend=KernelBackend.REFERENCE,
                min_vector_width=0,
                description="Scalar GEMM fallback"
            ),

            # VNNI INT8 dot product rules
            DispatchRule(
                workload=WorkloadType.DOT_PRODUCT,
                required_features={"avx512vnni", "avx512f", "avx512bw"},
                preferred_backend=KernelBackend.AVX512VNNI,
                fallback_backend=KernelBackend.AVX512F,
                min_vector_width=512,
                description="VNNI INT8 dot product"
            ),
            DispatchRule(
                workload=WorkloadType.DOT_PRODUCT,
                required_features={"avx512f", "avx512bw"},
                preferred_backend=KernelBackend.AVX512F,
                fallback_backend=KernelBackend.AVX2,
                min_vector_width=512,
                description="AVX-512 BW dot product"
            ),
            DispatchRule(
                workload=WorkloadType.DOT_PRODUCT,
                required_features={"avx2"},
                preferred_backend=KernelBackend.AVX2,
                fallback_backend=KernelBackend.AVX,
                min_vector_width=256,
                description="AVX2 dot product"
            ),

            # BF16 rules
            DispatchRule(
                workload=WorkloadType.GEMM,
                required_features={"amx_bf16", "amx_tile"},
                preferred_backend=KernelBackend.AMX_BF16,
                fallback_backend=KernelBackend.AVX512BF16,
                min_vector_width=1024,
                description="AMX BF16 GEMM"
            ),
            DispatchRule(
                workload=WorkloadType.GEMM,
                required_features={"avx512_bf16", "avx512f"},
                preferred_backend=KernelBackend.AVX512BF16,
                fallback_backend=KernelBackend.AVX512F,
                min_vector_width=512,
                description="AVX-512 BF16 GEMM"
            ),

            # AMX INT8 rules
            DispatchRule(
                workload=WorkloadType.GEMM,
                required_features={"amx_int8", "amx_tile"},
                preferred_backend=KernelBackend.AMX_INT8,
                fallback_backend=KernelBackend.AVX512VNNI,
                min_vector_width=1024,
                description="AMX INT8 GEMM"
            ),
            DispatchRule(
                workload=WorkloadType.GEMM,
                required_features={"amx_fp16", "amx_tile"},
                preferred_backend=KernelBackend.AMX_FP16,
                fallback_backend=KernelBackend.AVX512F,
                min_vector_width=1024,
                description="AMX FP16 GEMM"
            ),

            # Crypto rules
            DispatchRule(
                workload=WorkloadType.CRYPTO_AES,
                required_features={"aes", "vaes"},
                preferred_backend=KernelBackend.AVX512F,
                fallback_backend=KernelBackend.AVX2,
                min_vector_width=128,
                description="VAES / AES-NI"
            ),
            DispatchRule(
                workload=WorkloadType.CRYPTO_AES,
                required_features={"aes"},
                preferred_backend=KernelBackend.SSE42,
                fallback_backend=KernelBackend.SCALAR,
                min_vector_width=128,
                description="AES-NI"
            ),

            # SHA rules
            DispatchRule(
                workload=WorkloadType.CRYPTO_SHA,
                required_features={"sha"},
                preferred_backend=KernelBackend.AVX2,
                fallback_backend=KernelBackend.SSE42,
                min_vector_width=128,
                description="SHA extensions"
            ),

            # Compression rules
            DispatchRule(
                workload=WorkloadType.COMPRESSION,
                required_features={"avx2"},
                preferred_backend=KernelBackend.AVX2,
                fallback_backend=KernelBackend.SSE42,
                min_vector_width=256,
                description="AVX2 compression"
            ),

            # Element-wise rules
            DispatchRule(
                workload=WorkloadType.ELEMENTWISE,
                required_features={"avx512f"},
                preferred_backend=KernelBackend.AVX512F,
                fallback_backend=KernelBackend.AVX2,
                min_vector_width=512,
                description="AVX-512 element-wise"
            ),
            DispatchRule(
                workload=WorkloadType.ELEMENTWISE,
                required_features={"avx2"},
                preferred_backend=KernelBackend.AVX2,
                fallback_backend=KernelBackend.AVX,
                min_vector_width=256,
                description="AVX2 element-wise"
            ),
            DispatchRule(
                workload=WorkloadType.ELEMENTWISE,
                required_features={"avx"},
                preferred_backend=KernelBackend.AVX,
                fallback_backend=KernelBackend.SSE42,
                min_vector_width=256,
                description="AVX element-wise"
            ),

            # Reduction rules
            DispatchRule(
                workload=WorkloadType.REDUCTION,
                required_features={"avx512f", "avx512vl"},
                preferred_backend=KernelBackend.AVX512F,
                fallback_backend=KernelBackend.AVX2,
                min_vector_width=512,
                description="AVX-512 reduction"
            ),
            DispatchRule(
                workload=WorkloadType.REDUCTION,
                required_features={"avx2"},
                preferred_backend=KernelBackend.AVX2,
                fallback_backend=KernelBackend.AVX,
                min_vector_width=256,
                description="AVX2 reduction"
            ),

            # Convolution rules
            DispatchRule(
                workload=WorkloadType.CONVOLUTION,
                required_features={"avx512f", "avx512vnni"},
                preferred_backend=KernelBackend.AVX512VNNI,
                fallback_backend=KernelBackend.AVX512F,
                min_vector_width=512,
                description="VNNI convolution"
            ),
            DispatchRule(
                workload=WorkloadType.CONVOLUTION,
                required_features={"avx2"},
                preferred_backend=KernelBackend.AVX2,
                fallback_backend=KernelBackend.AVX,
                min_vector_width=256,
                description="AVX2 convolution"
            ),
        ]

    def _has_features(self, required: Set[str]) -> bool:
        """Check if all required features are present."""
        for feat in required:
            if not getattr(self.features, feat, False):
                return False
        return True

    def get_backend(self, workload: WorkloadType) -> KernelBackend:
        """Get the optimal backend for a workload."""
        if workload in self._cache:
            return self._cache[workload]

        for rule in self.rules:
            if rule.workload == workload and self._has_features(rule.required_features):
                self._cache[workload] = rule.preferred_backend
                return rule.preferred_backend

        # Fallback to reference
        self._cache[workload] = KernelBackend.REFERENCE
        return KernelBackend.REFERENCE

    def get_dispatch_info(self, workload: WorkloadType) -> Dict:
        """Get detailed dispatch information for a workload."""
        backend = self.get_backend(workload)

        # Find the matching rule
        matched_rule = None
        for rule in self.rules:
            if rule.workload == workload and self._has_features(rule.required_features):
                matched_rule = rule
                break

        return {
            "workload": workload.value,
            "selected_backend": backend.value,
            "matched_rule": matched_rule.description if matched_rule else "No rule matched (reference fallback)",
            "vector_width": matched_rule.min_vector_width if matched_rule else 0,
            "available_features": [f for f in matched_rule.required_features if self._has_features({f})] if matched_rule else [],
            "missing_features": [f for f in matched_rule.required_features if not self._has_features({f})] if matched_rule else [],
        }

    def get_all_dispatches(self) -> Dict[WorkloadType, Dict]:
        """Get dispatch info for all workload types."""
        return {w: self.get_dispatch_info(w) for w in WorkloadType}

    def print_dispatch_table(self) -> None:
        """Print a formatted dispatch table."""
        print(f"CPU Features: {self.features.vendor_id}")
        print(f"Enabled: {len(self.features.enabled_features())} features")
        print()
        print(f"{'Workload':<20} {'Backend':<15} {'Vector':<8} {'Rule'}")
        print("-" * 80)

        for workload in WorkloadType:
            info = self.get_dispatch_info(workload)
            print(f"{info['workload']:<20} {info['selected_backend']:<15} {info['vector_width']:<8} {info['matched_rule']}")


def create_dispatcher() -> KernelDispatcher:
    """Create a dispatcher for the current CPU."""
    features = get_cpuid()
    return KernelDispatcher(features)


def get_optimal_backend(workload: WorkloadType) -> KernelBackend:
    """Get optimal backend for a workload (convenience function)."""
    dispatcher = create_dispatcher()
    return dispatcher.get_backend(workload)


def dispatch_kernel(workload: WorkloadType, *args, **kwargs) -> any:
    """Dispatch a workload to the optimal kernel implementation.

    This is a placeholder for actual kernel dispatch. In production,
    this would call into compiled extensions (C/C++/Rust/ASM) via
    ctypes, cffi, or Python C API.
    """
    dispatcher = create_dispatcher()
    backend = dispatcher.get_backend(workload)

    # Map backend to actual kernel function
    kernel_map: Dict[KernelBackend, Callable] = {
        KernelBackend.REFERENCE: _kernel_reference,
        KernelBackend.SCALAR: _kernel_scalar,
        KernelBackend.SSE42: _kernel_sse42,
        KernelBackend.AVX: _kernel_avx,
        KernelBackend.AVX2: _kernel_avx2,
        KernelBackend.AVX512F: _kernel_avx512f,
        KernelBackend.AVX512VL: _kernel_avx512f,
        KernelBackend.AVX512BW: _kernel_avx512f,
        KernelBackend.AVX512DQ: _kernel_avx512f,
        KernelBackend.AVX512VNNI: _kernel_avx512vnni,
        KernelBackend.AVX512BF16: _kernel_avx512bf16,
        KernelBackend.AMX_TILE: _kernel_amx_tile,
        KernelBackend.AMX_INT8: _kernel_amx_int8,
        KernelBackend.AMX_BF16: _kernel_amx_bf16,
        KernelBackend.AMX_FP16: _kernel_amx_fp16,
    }

    kernel_func = kernel_map.get(backend, _kernel_reference)
    return kernel_func(workload, *args, **kwargs)


# Placeholder kernel implementations
def _kernel_reference(workload: WorkloadType, *args, **kwargs) -> str:
    """Reference implementation (pure Python)."""
    return f"reference_{workload.value}"


def _kernel_scalar(workload: WorkloadType, *args, **kwargs) -> str:
    """Optimized scalar implementation."""
    return f"scalar_{workload.value}"


def _kernel_sse42(workload: WorkloadType, *args, **kwargs) -> str:
    """SSE4.2 implementation."""
    return f"sse42_{workload.value}"


def _kernel_avx(workload: WorkloadType, *args, **kwargs) -> str:
    """AVX implementation."""
    return f"avx_{workload.value}"


def _kernel_avx2(workload: WorkloadType, *args, **kwargs) -> str:
    """AVX2 implementation."""
    return f"avx2_{workload.value}"


def _kernel_avx512f(workload: WorkloadType, *args, **kwargs) -> str:
    """AVX-512 Foundation implementation."""
    return f"avx512f_{workload.value}"


def _kernel_avx512vnni(workload: WorkloadType, *args, **kwargs) -> str:
    """AVX-512 VNNI implementation."""
    return f"avx512vnni_{workload.value}"


def _kernel_avx512bf16(workload: WorkloadType, *args, **kwargs) -> str:
    """AVX-512 BF16 implementation."""
    return f"avx512bf16_{workload.value}"


def _kernel_amx_tile(workload: WorkloadType, *args, **kwargs) -> str:
    """AMX Tile implementation."""
    return f"amx_tile_{workload.value}"


def _kernel_amx_int8(workload: WorkloadType, *args, **kwargs) -> str:
    """AMX INT8 implementation."""
    return f"amx_int8_{workload.value}"


def _kernel_amx_bf16(workload: WorkloadType, *args, **kwargs) -> str:
    """AMX BF16 implementation."""
    return f"amx_bf16_{workload.value}"


def _kernel_amx_fp16(workload: WorkloadType, *args, **kwargs) -> str:
    """AMX FP16 implementation."""
    return f"amx_fp16_{workload.value}"


if __name__ == "__main__":
    dispatcher = create_dispatcher()
    dispatcher.print_dispatch_table()

    print("\nDetailed GEMM dispatch:")
    import json
    print(json.dumps(dispatcher.get_dispatch_info(WorkloadType.GEMM), indent=2))

    print("\nDispatch test:")
    for workload in [WorkloadType.GEMM, WorkloadType.DOT_PRODUCT, WorkloadType.CRYPTO_AES]:
        result = dispatch_kernel(workload)
        print(f"  {workload.value} -> {result}")